"""Mem0 Memory and AsyncMemory: scoped fact memory, decisions, history and search on DuckDB.

Both clients share one synchronous core (`_MemoryCore`); AsyncMemory runs it in
worker threads. Off a borrowed handle each client owns its stores; on a borrowed
native DuckDB handle nothing here commits, rolls back, closes or opens a connection.
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
import warnings
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

import duckdb
from pydantic import ValidationError

from mem0.configs.base import MemoryConfig, MemoryItem
from mem0.configs.enums import MemoryType
from mem0.configs.prompts import (
    ADDITIVE_EXTRACTION_PROMPT,
    AGENT_CONTEXT_SUFFIX,
    PROCEDURAL_MEMORY_SYSTEM_PROMPT,
    generate_additive_extraction_prompt,
    get_update_memory_messages,
)
from mem0.exceptions import LLMError, VectorStoreError
from mem0.exceptions import ValidationError as Mem0ValidationError
from mem0.memory.base import MemoryBase
from mem0.memory.duckdb_storage import DuckDBManager
from mem0.memory.notices import (
    PERFORMANCE_SLOW_QUERY_THRESHOLD_SECONDS,
    detect_decay_usage_from_delete,
    detect_decay_usage_from_delete_all,
    detect_scale_threshold_from_add_result,
    detect_scale_threshold_from_top_k,
    detect_temporal_usage_from_metadata,
    detect_temporal_usage_from_search,
    display_decay_usage_notice,
    display_decay_usage_notice_async,
    display_first_run_notice,
    display_first_run_notice_async,
    display_performance_slow_query_notice,
    display_performance_slow_query_notice_async,
    display_scale_threshold_notice,
    display_scale_threshold_notice_async,
    display_temporal_usage_notice,
    display_temporal_usage_notice_async,
    get_decay_feature_error_message,
    get_decay_feature_error_message_async,
    get_temporal_feature_error_message,
    get_temporal_feature_error_message_async,
)
from mem0.memory.setup import mem0_dir, setup_config
from mem0.memory.telemetry import MEM0_TELEMETRY, capture_event
from mem0.memory.utils import (
    ensure_json_instruction,
    extract_json,
    get_fact_retrieval_messages,
    normalize_facts,
    parse_messages,
    parse_vision_messages,
    process_telemetry_filters,
    remove_code_blocks,
)
from mem0.utils.entity_extraction import extract_entities, extract_entities_batch
from mem0.utils.factory import (
    EmbedderFactory,
    LlmFactory,
    RerankerFactory,
    VectorStoreFactory,
)
from mem0.utils.lemmatization import lemmatize_for_bm25
from mem0.utils.scoring import (
    ENTITY_BOOST_WEIGHT,
    get_bm25_params,
    normalize_bm25,
    score_and_rank,
)
from mem0.vector_stores.base import VectorStoreBase

# Suppress SWIG deprecation warnings globally
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*SwigPy.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*swigvarlink.*")

# Initialize logger early for util functions
logger = logging.getLogger(__name__)


def _validate_memory_actions(actions, uuid_mapping):
    """Reject malformed or unscoped decisions before any planned memory mutation."""
    if not isinstance(actions, list):
        raise ValueError("Memory decisions must contain a list of actions")
    for action in actions:
        if not isinstance(action, dict) or action.get("event") not in {
            "ADD",
            "UPDATE",
            "DELETE",
            "NONE",
        }:
            raise ValueError("Unsupported memory action")
        if action["event"] != "ADD" and (
            not isinstance(action.get("id"), str) or action["id"] not in uuid_mapping
        ):
            raise ValueError("Memory action must reference a scoped retrieved memory ID")
        if not isinstance(action.get("text"), str) or not action["text"].strip():
            raise ValueError("Memory action must contain nonempty text")


def _require_scoped_memory(memory, filters):
    """Refuse missing or foreign search results before they can enter a mutation plan."""
    if memory is None or not isinstance(memory.payload, dict):
        raise ValueError("A scoped memory record is required")
    if any(
        memory.payload.get(key) != filters[key]
        for key in ("user_id", "agent_id", "run_id")
        if filters.get(key)
    ):
        raise ValueError("Retrieved memory does not belong to the requested identity scope")


def _with_session_ids(payload, metadata):
    """Return a copy of a memory payload carrying the agent/run ids metadata newly supplies.

    Used by a NONE decision: the text and embedding stay, only the session ids
    and updated_at change.
    """
    updated = deepcopy(payload)
    if metadata.get("agent_id"):
        updated["agent_id"] = metadata["agent_id"]
    if metadata.get("run_id"):
        updated["run_id"] = metadata["run_id"]
    updated["created_at"] = _normalize_iso_timestamp_to_utc(updated.get("created_at"))
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated


def _vector_store_list_rows(listed):
    """Return the stored rows of a list() result, whether flat or wrapped as [rows, ...]."""
    if isinstance(listed, (list, tuple)) and listed and isinstance(listed[0], list):
        return listed[0]
    if isinstance(listed, (list, tuple)):
        return listed
    return []


# Fields that hold runtime auth/connection objects and must be preserved.
# These are non-serializable objects (e.g. AWSV4SignerAuth, RequestsHttpConnection)
# needed by clients like OpenSearch — not sensitive strings to redact.
_RUNTIME_FIELDS = frozenset(
    {
        "http_auth",
        "auth",
        "connection_class",
        "ssl_context",
    }
)

# Fields that are known to contain sensitive secrets and must be redacted.
_SENSITIVE_FIELDS_EXACT = frozenset(
    {
        "api_key",
        "secret_key",
        "private_key",
        "access_key",
        "password",
        "credentials",
        "credential",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "auth_token",
        "session_token",
        "client_secret",
        "auth_client_secret",
        "azure_client_secret",
        "service_account_json",
        "aws_session_token",
    }
)

# Suffixes that indicate a field likely holds a secret value.
_SENSITIVE_SUFFIXES = (
    "_password",
    "_secret",
    "_token",
    "_credential",
    "_credentials",
)

# Entity parameters that must be passed via filters, not top-level kwargs
ENTITY_PARAMS = frozenset({"user_id", "agent_id", "run_id"})
DELETE_ALL_BATCH_SIZE = 1000

# Tenant-scoping fields that caller-supplied metadata must never set, on either the
# creation or the update path (issues #4490, #6277, #6655).
_IDENTITY_KEYS = ENTITY_PARAMS | {"actor_id"}


def _strip_identity_keys(
    metadata: Dict[str, Any],
    existing_payload: Dict[str, Any],
    *,
    context: str = "update()",
) -> Dict[str, Any]:
    """Drop identity keys from caller metadata; scope is set by the entity params, not metadata.

    On the update path `existing_payload` carries the memory's current scope, so
    re-sending an identical value is silently accepted; only a changed value warns.
    On the creation path there is no prior payload, so pass an empty dict and every
    identity key present in `metadata` is dropped with a warning.
    """
    clean = {}
    for key, value in metadata.items():
        if key not in _IDENTITY_KEYS:
            clean[key] = value
        elif value != existing_payload.get(key):
            logger.warning(
                f"{context}: ignoring metadata['{key}'] - identity fields cannot be set through metadata"
            )
    return clean


def _reject_top_level_entity_params(kwargs: Dict[str, Any], method_name: str) -> None:
    """Reject top-level entity parameters - must use filters instead."""
    invalid_keys = ENTITY_PARAMS & set(kwargs.keys())
    if invalid_keys:
        raise ValueError(
            f"Top-level entity parameters {invalid_keys} are not supported in {method_name}(). "
            f"Use filters={{'user_id': '...'}} instead."
        )


def _validate_and_trim_entity_id(value: Optional[Any], name: str) -> Optional[str]:
    """Validate and normalize an entity ID.

    - Coerces non-string values (e.g. integer ids) to str
    - Trims leading/trailing whitespace
    - Rejects empty or whitespace-only strings
    - Rejects strings containing internal whitespace

    Args:
        value: The entity ID value to validate
        name: The parameter name (for error messages)

    Returns:
        The trimmed entity ID, or None if input is None

    Raises:
        ValueError: If entity ID is invalid
    """
    if value is None:
        return None
    # Callers commonly pass integer ids (e.g. a database primary key). Coerce
    # to str at this single validation point so scoping stays consistent across
    # add/search/get_all/delete_all instead of crashing on `.strip()`.
    if not isinstance(value, str):
        value = str(value)
    trimmed = value.strip()
    if trimmed == "":
        raise ValueError(
            f"Invalid {name}: cannot be empty or whitespace-only. Provide a valid identifier."
        )
    if any(c.isspace() for c in trimmed):
        raise ValueError(
            f"Invalid {name}: cannot contain whitespace. Provide a valid identifier without spaces."
        )
    return trimmed


def _validate_search_params(threshold: Optional[float] = None, top_k: Optional[int] = None) -> None:
    """
    Validates search parameters.

    Args:
        threshold: Similarity threshold (must be between 0 and 1)
        top_k: Number of results to return (must be non-negative integer)

    Raises:
        ValueError: If threshold or top_k are invalid
    """
    if threshold is not None:
        if not isinstance(threshold, (int, float)):
            raise ValueError("threshold must be a valid number")
        if threshold < 0 or threshold > 1:
            raise ValueError(
                f"Invalid threshold: {threshold}. Must be between 0 and 1 (inclusive)."
            )
    if top_k is not None:
        if not isinstance(top_k, int) or isinstance(top_k, bool):
            raise ValueError("top_k must be a valid integer")
        if top_k < 0:
            raise ValueError(f"Invalid top_k: {top_k}. Must be a non-negative integer.")


def _validate_and_trim_search_query(query: str) -> str:
    """
    Validates and normalizes a search query before embedding/vector search.

    Raises:
        ValueError: If query is not a string or is empty/whitespace-only.
    """
    if not isinstance(query, str):
        raise ValueError("Invalid query: must be a non-empty string.")
    trimmed = query.strip()
    if not trimmed:
        raise ValueError("Invalid query: cannot be empty or whitespace-only.")
    return trimmed


def _is_sensitive_field(field_name: str) -> bool:
    """Check if a field should be redacted for telemetry safety.

    Uses a layered approach:
    1. Runtime fields (allowlist) — always preserved, highest priority.
    2. Exact deny list — known secret field names.
    3. Suffix deny list — catches patterns like db_password, auth_secret, etc.
    """
    name = field_name.lower().strip()
    if name in _RUNTIME_FIELDS:
        return False
    if name in _SENSITIVE_FIELDS_EXACT:
        return True
    return any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _safe_deepcopy_config(config):
    """Safely deepcopy config, falling back to dict-based cloning for non-serializable objects."""
    try:
        return deepcopy(config)
    except Exception:
        logger.debug("Deepcopy failed, using dict-based cloning", exc_info=True)

        config_class = type(config)

        if hasattr(config, "model_dump"):
            try:
                clone_dict = config.model_dump()
            except Exception:
                logger.debug("model_dump failed, cloning __dict__", exc_info=True)
                clone_dict = dict(config.__dict__)
        else:
            clone_dict = dict(config.__dict__)

        # Restore runtime fields, redact sensitive ones
        for field_name in list(clone_dict.keys()):
            if field_name in _RUNTIME_FIELDS and hasattr(config, field_name):
                clone_dict[field_name] = getattr(config, field_name)
            elif _is_sensitive_field(field_name):
                clone_dict[field_name] = None

        try:
            return config_class(**clone_dict)
        except Exception:
            logger.debug(
                "Config reconstruction failed, returning shallow dict clone", exc_info=True
            )
            return type("Config", (), clone_dict)()


def _normalize_iso_timestamp_to_utc(timestamp: Optional[str]) -> Optional[str]:
    """Normalize timezone-aware ISO timestamps to UTC without rewriting naive values."""
    if not timestamp:
        return timestamp
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if parsed.tzinfo is None:
        return timestamp
    return parsed.astimezone(timezone.utc).isoformat()


def _build_filters_and_metadata(
    *,  # Enforce keyword-only arguments
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    actor_id: Optional[str] = None,  # For query-time filtering
    input_metadata: Optional[Dict[str, Any]] = None,
    input_filters: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Constructs metadata for storage and filters for querying based on session and actor identifiers.

    This helper supports multiple session identifiers (`user_id`, `agent_id`, and/or `run_id`)
    for flexible session scoping and optionally narrows queries to a specific `actor_id`. It returns two dicts:

    1. `base_metadata_template`: Used as a template for metadata when storing new memories.
       It includes all provided session identifier(s) and any `input_metadata`. Identity
       scope is set from the entity params only; identity keys in `input_metadata` are
       dropped, so freeform metadata cannot place a memory into an unrequested scope.
    2. `effective_query_filters`: Used for querying existing memories. It includes all
       provided session identifier(s), any `input_filters`, and a resolved actor
       identifier for targeted filtering if specified by any actor-related inputs.

    Actor filtering precedence: explicit `actor_id` arg → `filters["actor_id"]`
    This resolved actor ID is used for querying but is not added to `base_metadata_template`,
    as the actor for storage is typically derived from message content at a later stage.

    Args:
        user_id (Optional[str]): User identifier, for session scoping.
        agent_id (Optional[str]): Agent identifier, for session scoping.
        run_id (Optional[str]): Run identifier, for session scoping.
        actor_id (Optional[str]): Explicit actor identifier, used as a potential source for
            actor-specific filtering. See actor resolution precedence in the main description.
        input_metadata (Optional[Dict[str, Any]]): Base dictionary to be augmented with
            session identifiers for the storage metadata template. Defaults to an empty dict.
        input_filters (Optional[Dict[str, Any]]): Base dictionary to be augmented with
            session and actor identifiers for query filters. Defaults to an empty dict.

    Returns:
        tuple[Dict[str, Any], Dict[str, Any]]: A tuple containing:
            - base_metadata_template (Dict[str, Any]): Metadata template for storing memories,
              scoped to the provided session(s).
            - effective_query_filters (Dict[str, Any]): Filters for querying memories,
              scoped to the provided session(s) and potentially a resolved actor.
    """

    # Identity scope is set below from the entity params only. Stripping the keys here
    # stops caller metadata from placing a memory into a scope the caller did not pass,
    # which the re-pins below cannot prevent for a param that was left unset (issue #6655).
    base_metadata_template = (
        _strip_identity_keys(deepcopy(input_metadata), {}, context="add()")
        if input_metadata
        else {}
    )
    effective_query_filters = deepcopy(input_filters) if input_filters else {}

    # ---------- validate and add all provided session ids ----------
    session_ids_provided = []

    # Validate and trim entity IDs
    user_id = _validate_and_trim_entity_id(user_id, "user_id")
    agent_id = _validate_and_trim_entity_id(agent_id, "agent_id")
    run_id = _validate_and_trim_entity_id(run_id, "run_id")

    if user_id:
        base_metadata_template["user_id"] = user_id
        effective_query_filters["user_id"] = user_id
        session_ids_provided.append("user_id")

    if agent_id:
        base_metadata_template["agent_id"] = agent_id
        effective_query_filters["agent_id"] = agent_id
        session_ids_provided.append("agent_id")

    if run_id:
        base_metadata_template["run_id"] = run_id
        effective_query_filters["run_id"] = run_id
        session_ids_provided.append("run_id")

    if not session_ids_provided:
        raise Mem0ValidationError(
            message="At least one of 'user_id', 'agent_id', or 'run_id' must be provided.",
            error_code="VALIDATION_001",
            details={"provided_ids": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}},
            suggestion="Please provide at least one identifier to scope the memory operation.",
        )

    # ---------- optional actor filter ----------
    resolved_actor_id = actor_id or effective_query_filters.get("actor_id")
    if resolved_actor_id:
        effective_query_filters["actor_id"] = resolved_actor_id

    return base_metadata_template, effective_query_filters


def _escape_scope_value(val: Any) -> str:
    """Escape the structural delimiters of the session scope key."""
    return str(val).replace("%", "%25").replace("&", "%26").replace("=", "%3D")


def _build_session_scope(filters):
    """Build deterministic session scope string from entity IDs."""
    parts = []
    for key in sorted(["user_id", "agent_id", "run_id"]):
        val = filters.get(key)
        if val:
            parts.append(f"{key}={_escape_scope_value(val)}")
    return "&".join(parts)


def _entity_collection_name(provider: str, collection_name: str) -> str:
    separator = "-" if provider == "s3_vectors" else "_"
    return f"{collection_name}{separator}entities"


def _normalize_expiration_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("expiration_date must be a valid date in YYYY-MM-DD format.") from exc
    raise ValueError("expiration_date must be a date string in YYYY-MM-DD format.")


def _payload_is_expired(payload: Optional[Dict[str, Any]]) -> bool:
    if not payload:
        return False
    expiration_date = payload.get("expiration_date")
    if not expiration_date:
        return False
    try:
        return date.fromisoformat(str(expiration_date)) < datetime.now(timezone.utc).date()
    except ValueError:
        return False


setup_config()
logger = logging.getLogger(__name__)

_UNSET = object()
# Serializes lazy entity-store creation, so threads sharing a client never open two stores.
_ENTITY_STORE_LOCK = threading.Lock()
_PROJECT_UPDATE_UNSUPPORTED_ERROR = "Project updates are not supported by the OSS Memory SDK."


class _OSSProject:
    def update(
        self,
        custom_instructions: Optional[str] = None,
        custom_categories: Optional[list] = None,
        multilingual: Optional[bool] = None,
        decay: Optional[bool] = None,
    ):
        if decay is True:
            raise ValueError(get_decay_feature_error_message("sync", "project.update", "decay"))
        raise ValueError(_PROJECT_UPDATE_UNSUPPORTED_ERROR)


class _AsyncOSSProject:
    async def update(
        self,
        custom_instructions: Optional[str] = None,
        custom_categories: Optional[list] = None,
        multilingual: Optional[bool] = None,
        decay: Optional[bool] = None,
    ):
        if decay is True:
            raise ValueError(
                await get_decay_feature_error_message_async("async", "project.update", "decay")
            )
        raise ValueError(_PROJECT_UPDATE_UNSUPPORTED_ERROR)


class _MemoryCore:
    """The storage, entity, decision and search steps shared by Memory and AsyncMemory.

    Every method here is synchronous: Memory calls it directly and AsyncMemory
    runs it in a worker thread, so both clients share one implementation of each
    write, entity update, decision and search. The concrete client sets config,
    llm, embedding_model, vector_store, db, custom_instructions, api_version,
    _entity_store, _borrowed_connection, _planned_embeddings and
    planned_embedding_misses. On a borrowed handle nothing here commits, rolls
    back, closes or opens a connection.
    """

    config: MemoryConfig
    vector_store: Any
    db: Optional[DuckDBManager]
    _entity_store: Any
    _borrowed_connection: Optional[duckdb.DuckDBPyConnection]
    _planned_embeddings: Optional[Dict[Any, Any]]
    planned_embedding_misses: list

    @property
    def entity_store(self):
        """Lazily initialize entity store on first use (once, even across threads)."""
        with _ENTITY_STORE_LOCK:
            if self._entity_store is None:
                self._entity_store = self._create_entity_store()
        return self._entity_store

    def _create_entity_store(self):
        """Create the entity collection's store beside the memory collection, on the same handle if borrowed."""
        entity_config = _safe_deepcopy_config(self.config.vector_store.config)
        entity_collection = _entity_collection_name(
            self.config.vector_store.provider, self.collection_name
        )
        # Set collection name on the cloned config
        if hasattr(entity_config, "collection_name"):
            entity_config.collection_name = entity_collection
        elif isinstance(entity_config, dict):
            entity_config["collection_name"] = entity_collection
        # For Qdrant, share the existing client to avoid RocksDB lock contention
        # when using embedded mode (path=...). QdrantConfig.client takes precedence
        # over host/port/path.
        if self.config.vector_store.provider == "qdrant" and hasattr(self.vector_store, "client"):
            if hasattr(entity_config, "client"):
                entity_config.client = self.vector_store.client
            elif isinstance(entity_config, dict):
                entity_config["client"] = self.vector_store.client
        return VectorStoreFactory.create(
            self.config.vector_store.provider,
            entity_config,
            **({"connection": self._borrowed_connection} if self._borrowed_connection else {}),
        )

    @contextmanager
    def use_planned_embeddings(self, embeddings):
        """Within the block, entity maintenance embeds only from `embeddings`.

        Keys are (text, purpose) pairs. A text outside the set is not sent to the
        model: it is appended to `planned_embedding_misses` and the entity step
        fails, so a caller applying inside a transaction can detect the gap and
        roll back instead of leaving stale entity records.
        """
        self._planned_embeddings = dict(embeddings)
        self.planned_embedding_misses = []
        try:
            yield
        finally:
            self._planned_embeddings = None

    @property
    def _strict(self):
        """True on a borrowed handle: required writes and entity upkeep raise, never degrade."""
        return self._borrowed_connection is not None

    def _entity_list(self, filters):
        """List entity records within `filters`.

        On DuckDB, and on any borrowed handle, the scan is sized from the entity
        store's actual row count, so no record is missed because of a cap. Other
        standalone providers keep upstream's fixed scan size.
        """
        if not self._strict and self.config.vector_store.provider != "duckdb":
            return self.entity_store.list(filters=filters, top_k=10000)
        return self.entity_store.list(
            filters=filters, top_k=max(self.entity_store.col_info()["count"], 1)
        )

    def _entity_embed(self, text, purpose):
        """Embed an entity text, from the prepared set when one is active."""
        if self._planned_embeddings is None:
            return self.embedding_model.embed(text, purpose)
        if (text, purpose) not in self._planned_embeddings:
            self.planned_embedding_misses.append((text, purpose))
            raise RuntimeError(f"Entity text was not embedded before the transaction: {text!r}")
        return self._planned_embeddings[(text, purpose)]

    def _entity_texts_to_embed(self, texts):
        """Return the (text, purpose) pairs `_link_entities_for_memory` will embed for these memory texts."""
        needed = []
        for text in texts:
            seen = set()
            for _entity_type, entity_text in extract_entities(text):
                key = self._normalize_entity_text(entity_text)
                if key and key not in seen:
                    seen.add(key)
                    needed.append((entity_text, "add"))
        return needed

    @staticmethod
    def _normalize_entity_text(value: str) -> str:
        """Return entity text lower-cased with single spaces, the key entity records match on."""
        return " ".join(value.strip().lower().split())

    def _existing_entities_by_text(self, filters):
        """Return existing entity rows keyed by normalized payload data."""
        try:
            listed = self._entity_list(filters)
        except Exception as e:
            if self._strict:
                raise
            logger.debug(f"Exact entity lookup failed, falling back to semantic dedup: {e}")
            return {}

        rows_by_text = {}
        for row in _vector_store_list_rows(listed):
            payload = getattr(row, "payload", None) or {}
            text = payload.get("data")
            if not isinstance(text, str):
                continue
            normalized = self._normalize_entity_text(text)
            if normalized and normalized not in rows_by_text:
                rows_by_text[normalized] = row
        return rows_by_text

    def _upsert_entity(self, entity_text, entity_type, memory_id, filters):
        """Upsert an entity into the entity store, linking it to a memory."""
        try:
            entity_embedding = self._entity_embed(entity_text, "add")
            search_filters = {
                k: v for k, v in filters.items() if k in ("user_id", "agent_id", "run_id") and v
            }
            exact_match = self._existing_entities_by_text(search_filters).get(
                self._normalize_entity_text(entity_text)
            )

            existing = []
            if exact_match is None:
                existing = self.entity_store.search(
                    query=entity_text,
                    vectors=entity_embedding,
                    top_k=1,
                    filters=search_filters,
                )

            semantic_match = existing[0] if existing and existing[0].score >= 0.95 else None
            match = exact_match or semantic_match
            if match:
                # Update existing entity's linked_memory_ids
                payload = match.payload or {}
                linked_ids = payload.get("linked_memory_ids", [])
                if memory_id not in linked_ids:
                    linked_ids.append(memory_id)
                    payload["linked_memory_ids"] = linked_ids
                    self.entity_store.update(
                        vector_id=match.id,
                        vector=None,
                        payload=payload,
                    )
            else:
                # Create new entity
                entity_id = str(uuid.uuid4())
                entity_payload = {
                    "data": entity_text,
                    "entity_type": entity_type,
                    "linked_memory_ids": [memory_id],
                    **{k: v for k, v in search_filters.items()},
                }
                self.entity_store.insert(
                    vectors=[entity_embedding],
                    ids=[entity_id],
                    payloads=[entity_payload],
                )
        except Exception as e:
            if self._strict:
                raise
            logger.warning(f"Entity upsert failed for '{entity_text}': {e}")

    def _link_entities_for_memory(self, memory_id, text, filters):
        """Extract entities from `text` and link them to `memory_id` in the
        entity store, scoped to `filters`. Simpler single-memory variant of
        Phase 7 in add(): per-entity search-then-update-or-insert via the
        existing `_upsert_entity` helper. Non-fatal on any failure, except on a
        borrowed handle, where failures raise.
        """
        try:
            entities = extract_entities(text)
            if not entities:
                return
            seen = set()
            for entity_type, entity_text in entities:
                key = self._normalize_entity_text(entity_text)
                if not key or key in seen:
                    continue
                seen.add(key)
                try:
                    self._upsert_entity(entity_text, entity_type, memory_id, filters)
                except Exception as e:
                    if self._strict:
                        raise
                    logger.debug(f"Entity link failed for '{entity_text}': {e}")
        except Exception as e:
            if self._strict:
                raise
            logger.warning(f"Entity linking failed for memory_id={memory_id}: {e}")

    def _should_use_agent_memory_extraction(self, messages, metadata):
        """Determine whether to use agent memory extraction.

        - If agent_id is present and messages contain assistant role -> True
        - Otherwise -> False

        Args:
            messages: List of message dictionaries
            metadata: Metadata containing user_id, agent_id, etc.

        Returns:
            bool: True if should use agent memory extraction, False for user memory extraction
        """
        # Check if agent_id is present in metadata
        has_agent_id = metadata.get("agent_id") is not None

        # Check if there are assistant role messages
        has_assistant_messages = any(msg.get("role") == "assistant" for msg in messages)

        # Use agent memory extraction if agent_id is present and there are assistant messages
        return has_agent_id and has_assistant_messages

    @staticmethod
    def _identity_filters(filters):
        """Return only the identity keys that scope a memory search."""
        return {key: filters[key] for key in ("user_id", "agent_id", "run_id") if filters.get(key)}

    def _extract_facts(self, messages, metadata, filters, prompt=None):
        """Stage 1 of the 1.0.11 action flow: one model call that turns messages into facts.

        It reads nothing from the database and writes nothing.
        """
        parsed_messages = parse_messages(messages)

        fact_prompt = prompt or self.config.custom_fact_extraction_prompt
        if fact_prompt:
            system_prompt = fact_prompt
            user_prompt = f"Input:\n{parsed_messages}"
        else:
            # Determine if this should use agent memory extraction based on agent_id presence
            # and role types in messages
            is_agent_memory = self._should_use_agent_memory_extraction(messages, metadata)
            system_prompt, user_prompt = get_fact_retrieval_messages(
                parsed_messages, is_agent_memory
            )

        if self.custom_instructions:
            system_prompt += "\n" + self.custom_instructions

        # Ensure 'json' appears in prompts for json_object response format compatibility
        system_prompt, user_prompt = ensure_json_instruction(system_prompt, user_prompt)

        response = self.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        try:
            cleaned_response = remove_code_blocks(response)
            if not cleaned_response.strip():
                new_retrieved_facts = []
            else:
                try:
                    # First try direct JSON parsing
                    new_retrieved_facts = json.loads(cleaned_response, strict=False)["facts"]
                except json.JSONDecodeError:
                    # Try extracting JSON from response (handles chatty LLM output)
                    extracted_json = extract_json(response)
                    new_retrieved_facts = json.loads(extracted_json, strict=False)["facts"]
                new_retrieved_facts = normalize_facts(new_retrieved_facts)
        except Exception as e:
            logger.error(f"Error in new_retrieved_facts: {e}")
            raise LLMError(f"Invalid fact extraction response: {e}") from e

        return new_retrieved_facts

    def _embed_facts(self, facts):
        """Embed each fact for the "add" purpose; returns {fact: embedding}. No database access."""
        return {fact: self.embedding_model.embed(fact, "add") for fact in facts}

    def _find_existing_memories(self, facts, embeddings, filters):
        """Stage 2: read the scoped memories each fact is nearest to (read only).

        Every result is checked against the requested identity scope before it
        can enter a decision, and expired memories are left out. Returns the unique results as {"id", "text"} rows
        keyed by their real ids.
        """
        search_filters = self._identity_filters(filters)
        found = {}
        for fact in facts:
            for mem in self.vector_store.search(
                query=fact, vectors=embeddings[fact], top_k=5, filters=search_filters
            ):
                _require_scoped_memory(mem, search_filters)
                if _payload_is_expired(mem.payload):
                    # Expired memories are hidden from their user; a decision must not
                    # update, keep or delete one in place of storing the new fact.
                    continue
                found[mem.id] = {"id": mem.id, "text": mem.payload.get("data", "")}
        logger.info(f"Total existing memories: {len(found)}")
        return list(found.values())

    def _decide_memory_actions(self, existing, new_retrieved_facts):
        """Stage 3: one model call that chooses ADD/UPDATE/DELETE/NONE for each fact.

        Existing ids are replaced by small integers before the call so a model
        cannot invent a real id. Returns (decisions, integer-id -> real id map);
        the decisions are validated against that map before they are returned.
        """
        retrieved_old_memory = [dict(item) for item in existing]
        temp_uuid_mapping = {}
        for idx, item in enumerate(retrieved_old_memory):
            temp_uuid_mapping[str(idx)] = item["id"]
            retrieved_old_memory[idx]["id"] = str(idx)

        if new_retrieved_facts:
            function_calling_prompt = get_update_memory_messages(
                retrieved_old_memory, new_retrieved_facts, self.config.custom_update_memory_prompt
            )

            try:
                response = self.llm.generate_response(
                    messages=[{"role": "user", "content": function_calling_prompt}],
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                logger.error(f"Error in new memory actions response: {e}")
                raise LLMError(f"Memory decision request failed: {e}") from e

            try:
                if not response or not response.strip():
                    logger.warning("Empty response from LLM, no memories to extract")
                    new_memories_with_actions = {}
                else:
                    try:
                        new_memories_with_actions = json.loads(
                            remove_code_blocks(response), strict=False
                        )
                    except json.JSONDecodeError:
                        extracted_json = extract_json(response)
                        new_memories_with_actions = json.loads(extracted_json, strict=False)
            except Exception as e:
                logger.error(f"Invalid JSON response: {e}")
                raise LLMError(f"Invalid memory decision response: {e}") from e
        else:
            new_memories_with_actions = {}

        _validate_memory_actions(new_memories_with_actions.get("memory", []), temp_uuid_mapping)
        return new_memories_with_actions, temp_uuid_mapping

    def _require_live_scoped_target(self, memory_id, search_filters):
        """Re-read a memory right before changing it and refuse a missing or out-of-scope one.

        The decision was made on an earlier read; the memory may have been
        changed or removed since. Raises ValueError so nothing is written.
        """
        _require_scoped_memory(self.vector_store.get(vector_id=memory_id), search_filters)

    def _add_with_memory_actions(self, messages, metadata, filters, prompt=None):
        """Run the maintained 1.0.11 fact and action flow using current mutation helpers."""
        new_retrieved_facts = self._extract_facts(messages, metadata, filters, prompt=prompt)
        if not new_retrieved_facts:
            logger.debug("No new facts retrieved from input. Skipping memory update LLM call.")
        new_message_embeddings = self._embed_facts(new_retrieved_facts)
        existing = self._find_existing_memories(
            new_retrieved_facts, new_message_embeddings, filters
        )
        decisions, mapping = self._decide_memory_actions(existing, new_retrieved_facts)
        returned_memories = self._execute_memory_actions(
            decisions, mapping, new_message_embeddings, metadata, filters
        )
        self.db.save_messages(messages, _build_session_scope(filters))
        return returned_memories

    def _get_all_from_vector_store(self, filters, limit, show_expired=False, output_limit=None):
        """List memories within filters as result dicts, leaving out expired ones unless asked."""
        memories_result = self.vector_store.list(filters=filters, top_k=limit)

        # Handle different vector store return formats by inspecting first element
        if isinstance(memories_result, (tuple, list)) and len(memories_result) > 0:
            first_element = memories_result[0]

            # If first element is a container, unwrap one level
            if isinstance(first_element, (list, tuple)):
                actual_memories = first_element
            else:
                # First element is a memory object, structure is already flat
                actual_memories = memories_result
        else:
            actual_memories = memories_result

        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
            "attributed_to",
            "expiration_date",
        ]
        core_and_promoted_keys = {
            "data",
            "hash",
            "created_at",
            "updated_at",
            "id",
            "text_lemmatized",
            "attributed_to",
            *promoted_payload_keys,
        }

        formatted_memories = []
        for mem in actual_memories:
            if not show_expired and _payload_is_expired(mem.payload):
                continue
            memory_item_dict = MemoryItem(
                id=mem.id,
                memory=mem.payload.get("data", ""),
                hash=mem.payload.get("hash"),
                created_at=mem.payload.get("created_at"),
                updated_at=mem.payload.get("updated_at"),
            ).model_dump(exclude={"score"})

            for key in promoted_payload_keys:
                if key in mem.payload:
                    memory_item_dict[key] = mem.payload[key]

            additional_metadata = {
                k: v for k, v in mem.payload.items() if k not in core_and_promoted_keys
            }
            if additional_metadata:
                memory_item_dict["metadata"] = additional_metadata

            formatted_memories.append(memory_item_dict)
            if output_limit is not None and len(formatted_memories) >= output_limit:
                break

        return formatted_memories

    def _process_metadata_filters(self, metadata_filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process enhanced metadata filters and convert them to vector store compatible format.

        Args:
            metadata_filters: Enhanced metadata filters with operators

        Returns:
            Dict of processed filters compatible with vector store
        """
        processed_filters = {}

        def process_condition(key: str, condition: Any) -> Dict[str, Any]:
            if not isinstance(condition, dict):
                # Simple equality: {"key": "value"}
                if condition == "*":
                    # Wildcard: match everything for this field (implementation depends on vector store)
                    return {key: "*"}
                return {key: condition}

            result = {}
            for operator, value in condition.items():
                # Map platform operators to universal format that can be translated by each vector store
                operator_map = {
                    "eq": "eq",
                    "ne": "ne",
                    "gt": "gt",
                    "gte": "gte",
                    "lt": "lt",
                    "lte": "lte",
                    "in": "in",
                    "nin": "nin",
                    "contains": "contains",
                    "icontains": "icontains",
                }

                if operator in operator_map:
                    result.setdefault(key, {})[operator_map[operator]] = value
                else:
                    raise ValueError(f"Unsupported metadata filter operator: {operator}")
            return result

        def merge_filters(target: Dict[str, Any], source: Dict[str, Any]) -> None:
            """Merge source into target, deep-merging nested operator dicts for the same key."""
            for key, value in source.items():
                if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                    target[key].update(value)
                else:
                    target[key] = value

        for key, value in metadata_filters.items():
            if key == "AND":
                # Logical AND: combine multiple conditions
                if not isinstance(value, list):
                    raise ValueError("AND operator requires a list of conditions")
                for condition in value:
                    for sub_key, sub_value in condition.items():
                        merge_filters(processed_filters, process_condition(sub_key, sub_value))
            elif key == "OR":
                # Logical OR: Pass through to vector store for implementation-specific handling
                if not isinstance(value, list) or not value:
                    raise ValueError("OR operator requires a non-empty list of conditions")
                # Store OR conditions in a way that vector stores can interpret
                processed_filters["$or"] = []
                for condition in value:
                    or_condition = {}
                    for sub_key, sub_value in condition.items():
                        merge_filters(or_condition, process_condition(sub_key, sub_value))
                    processed_filters["$or"].append(or_condition)
            elif key == "NOT":
                # Logical NOT: Pass through to vector store for implementation-specific handling
                if not isinstance(value, list) or not value:
                    raise ValueError("NOT operator requires a non-empty list of conditions")
                processed_filters["$not"] = []
                for condition in value:
                    not_condition = {}
                    for sub_key, sub_value in condition.items():
                        merge_filters(not_condition, process_condition(sub_key, sub_value))
                    processed_filters["$not"].append(not_condition)
            else:
                merge_filters(processed_filters, process_condition(key, value))

        return processed_filters

    def _has_advanced_operators(self, filters: Dict[str, Any]) -> bool:
        """
        Check if filters contain advanced operators that need special processing.

        Args:
            filters: Dictionary of filters to check

        Returns:
            bool: True if advanced operators are detected
        """
        if not isinstance(filters, dict):
            return False

        for key, value in filters.items():
            # Check for platform-style logical operators
            if key in ["AND", "OR", "NOT"]:
                return True
            # Check for comparison operators (without $ prefix for universal compatibility)
            if isinstance(value, dict):
                for op in value.keys():
                    if op in [
                        "eq",
                        "ne",
                        "gt",
                        "gte",
                        "lt",
                        "lte",
                        "in",
                        "nin",
                        "contains",
                        "icontains",
                    ]:
                        return True
            # Check for wildcard values
            if value == "*":
                return True
        return False

    def _search_vector_store(
        self, query, filters, limit, threshold=0.1, explain=False, show_expired=False
    ):
        """Rank memories within filters by semantic, keyword and entity signals; return result dicts."""
        # Guard against None threshold (backward compat)
        if threshold is None:
            threshold = 0.1

        # Step 1: Preprocess query
        query_lemmatized = lemmatize_for_bm25(query)
        query_entities = extract_entities(query)

        # Step 2: Embed query
        embeddings = self.embedding_model.embed(query, "search")

        # Step 3: Semantic search (over-fetch for scoring pool)
        internal_limit = max(limit * 4, 60)
        semantic_results = self.vector_store.search(
            query=query, vectors=embeddings, top_k=internal_limit, filters=filters
        )

        # Step 4: Keyword search (if store supports it)
        keyword_results = self.vector_store.keyword_search(
            query=query_lemmatized, top_k=internal_limit, filters=filters
        )

        # Step 5: Compute BM25 scores from keyword results
        bm25_scores = {}
        if keyword_results is not None:
            midpoint, steepness = get_bm25_params(query, lemmatized=query_lemmatized)
            for mem in keyword_results:
                mem_id = str(mem.id) if hasattr(mem, "id") else str(mem.get("id", ""))
                raw_score = mem.score if hasattr(mem, "score") else mem.get("score", 0)
                if raw_score and raw_score > 0:
                    bm25_scores[mem_id] = normalize_bm25(raw_score, midpoint, steepness)

        # Step 6: Compute entity boosts
        entity_boosts = {}
        if query_entities:
            entity_boosts = self._compute_entity_boosts(query_entities, filters)

        # Step 7: Build candidate set from semantic results
        candidates = []
        for mem in semantic_results:
            payload = mem.payload if hasattr(mem, "payload") else {}
            if not show_expired and _payload_is_expired(payload):
                continue
            mem_id = str(mem.id)
            candidates.append(
                {
                    "id": mem_id,
                    "score": mem.score,
                    "payload": payload,
                }
            )

        # Step 8: Score and rank
        scored_results = score_and_rank(
            semantic_results=candidates,
            bm25_scores=bm25_scores,
            entity_boosts=entity_boosts,
            threshold=threshold,
            top_k=limit,
            explain=explain,
        )

        # Step 9: Format results
        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
            "attributed_to",
            "expiration_date",
        ]
        core_and_promoted_keys = {
            "data",
            "hash",
            "created_at",
            "updated_at",
            "id",
            "text_lemmatized",
            "attributed_to",
            *promoted_payload_keys,
        }

        original_memories = []
        for scored in scored_results:
            payload = scored.get("payload") or {}

            if not payload.get("data"):
                continue  # Skip candidates with no payload data

            memory_item_dict = MemoryItem(
                id=scored["id"],
                memory=payload.get("data", ""),
                hash=payload.get("hash"),
                created_at=payload.get("created_at"),
                updated_at=payload.get("updated_at"),
                score=scored["score"],
            ).model_dump()

            for key in promoted_payload_keys:
                if key in payload:
                    memory_item_dict[key] = payload[key]

            additional_metadata = {
                k: v for k, v in payload.items() if k not in core_and_promoted_keys
            }
            if additional_metadata:
                if not memory_item_dict.get("metadata"):
                    memory_item_dict["metadata"] = {}
                memory_item_dict["metadata"].update(additional_metadata)
            if explain and "score_details" in scored:
                memory_item_dict["score_details"] = scored["score_details"]

            original_memories.append(memory_item_dict)

        return original_memories

    def _create_memory(self, data, existing_embeddings, metadata=None):
        """Store one memory with its payload and ADD history row; return its new id.

        Uses the embedding in existing_embeddings when present, else embeds the text.
        """
        logger.debug(f"Creating memory with {data=}")
        if data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = self.embedding_model.embed(data, memory_action="add")
        memory_id = str(uuid.uuid4())
        new_metadata = deepcopy(metadata) if metadata is not None else {}
        new_metadata["data"] = data
        new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        if "created_at" not in new_metadata:
            new_metadata["created_at"] = datetime.now(timezone.utc).isoformat()
        new_metadata["updated_at"] = new_metadata["created_at"]
        new_metadata["text_lemmatized"] = lemmatize_for_bm25(data)

        self.vector_store.insert(
            vectors=[embeddings],
            ids=[memory_id],
            payloads=[new_metadata],
        )
        self.db.add_history(
            memory_id,
            None,
            data,
            "ADD",
            created_at=new_metadata.get("created_at"),
            updated_at=new_metadata.get("updated_at"),
            actor_id=new_metadata.get("actor_id"),
            role=new_metadata.get("role"),
        )
        return memory_id

    def _update_memory(self, memory_id, data, existing_embeddings, metadata=None):
        """Rewrite one memory's text and metadata, record UPDATE history and relink entities.

        Identity keys in metadata are ignored. Raises ValueError for a missing
        memory or one without text; a store failure is raised unchanged.
        """
        logger.info(f"Updating memory with {data=}")

        try:
            existing_memory = self.vector_store.get(vector_id=memory_id)
        except Exception:
            # Backing-store failure, not a bad memory_id: re-raise the original so the REST layer maps it to 5xx, not 4xx.
            logger.error(f"Error getting memory with ID {memory_id} during update.")
            raise

        if existing_memory is None:
            raise ValueError(
                f"Memory with id {memory_id} not found. Please provide a valid 'memory_id'"
            )

        prev_value = existing_memory.payload.get("data")
        if data is None:
            data = prev_value
        if not isinstance(data, str):
            raise ValueError(f"Memory with id {memory_id} does not have text content to update")
        text_changed = data != prev_value

        new_metadata = deepcopy(existing_memory.payload)
        if metadata is not None:
            new_metadata.update(_strip_identity_keys(metadata, existing_memory.payload))

        new_metadata["data"] = data
        new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        new_metadata["text_lemmatized"] = lemmatize_for_bm25(data)
        new_metadata["created_at"] = existing_memory.payload.get("created_at")
        new_metadata["updated_at"] = datetime.now(timezone.utc).isoformat()

        if data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = self.embedding_model.embed(data, "update")

        self.vector_store.update(
            vector_id=memory_id,
            vector=embeddings,
            payload=new_metadata,
        )
        logger.info(f"Updating memory with ID {memory_id=} with {data=}")

        self.db.add_history(
            memory_id,
            prev_value,
            data,
            "UPDATE",
            created_at=new_metadata["created_at"],
            updated_at=new_metadata["updated_at"],
            actor_id=new_metadata.get("actor_id"),
            role=new_metadata.get("role"),
        )

        # Entity-store cleanup: strip this memory's id from old-text entities,
        # then re-extract entities from the new text and link them back.
        session_filters = {
            k: new_metadata[k] for k in ("user_id", "agent_id", "run_id") if new_metadata.get(k)
        }
        if text_changed:
            self._remove_memories_from_entity_store([memory_id], session_filters)
            self._link_entities_for_memory(memory_id, data, session_filters)

        return memory_id

    def _delete_memory(self, memory_id, existing_memory=None, unlink_entities=True):
        """Delete one memory, record its DELETE history row and, by default, unlink its entities.

        Raises ValueError when the memory does not exist. Pass existing_memory to
        skip the re-read, and unlink_entities=False when the caller unlinks a
        whole batch afterwards.
        """
        logger.info(f"Deleting memory with {memory_id=}")
        if existing_memory is None:
            existing_memory = self.vector_store.get(vector_id=memory_id)
            if existing_memory is None:
                raise ValueError(
                    f"Memory with id {memory_id} not found. Please provide a valid 'memory_id'"
                )
        prev_value = existing_memory.payload.get("data", "")
        created_at = _normalize_iso_timestamp_to_utc(existing_memory.payload.get("created_at"))
        updated_at = datetime.now(timezone.utc).isoformat()
        payload = existing_memory.payload or {}
        session_filters = {
            k: payload[k] for k in ("user_id", "agent_id", "run_id") if payload.get(k)
        }
        self.vector_store.delete(vector_id=memory_id)
        self.db.add_history(
            memory_id,
            prev_value,
            None,
            "DELETE",
            created_at=created_at,
            updated_at=updated_at,
            actor_id=existing_memory.payload.get("actor_id"),
            role=existing_memory.payload.get("role"),
            is_deleted=1,
        )

        # Entity-store cleanup: strip this memory's id from any entity records
        # that linked to it (fatal only on a borrowed handle).
        if unlink_entities:
            self._remove_memories_from_entity_store([memory_id], session_filters)

        return memory_id

    def _entities_linked_to(self, memory_ids, scope):
        """List the entity records within scope that may link one of memory_ids (read only).

        On DuckDB the scan is narrowed to rows whose stored link list contains
        one of the quoted ids; callers still check membership exactly, so the
        narrowing can only save work, never change the result.
        """
        filters = dict(scope)
        if self.config.vector_store.provider == "duckdb":
            filters["$or"] = [
                {"linked_memory_ids": {"contains": json.dumps(str(memory_id))}}
                for memory_id in memory_ids
            ]
        return _vector_store_list_rows(self._entity_list(filters))

    def _remove_memories_from_entity_store(self, memory_ids, filters, scan_scope=False):
        """Unlink memory_ids from every entity record within the identity scope of filters.

        An entity left with no link is deleted, so text derived only from removed
        memories does not survive; any other keeps its text and embedding and has
        its link list rewritten. scan_scope reads the whole scope once instead of
        narrowing by id, for large deletions. The entity store is maintained even
        if this process has not used it yet, so entities written before a restart
        are unlinked too. On a borrowed handle any failure raises so the caller's
        transaction rolls back; otherwise it is logged and the memory change stands.
        """
        removed = {str(memory_id) for memory_id in memory_ids}
        if not removed:
            return
        scope = self._identity_filters(filters)
        try:
            rows = (
                _vector_store_list_rows(self._entity_list(scope))
                if scan_scope
                else self._entities_linked_to(sorted(removed), scope)
            )
            for row in rows:
                payload = getattr(row, "payload", None) or {}
                linked = payload.get("linked_memory_ids", [])
                if not isinstance(linked, list) or removed.isdisjoint(linked):
                    continue
                remaining = [memory_id for memory_id in linked if memory_id not in removed]
                if remaining:
                    self.entity_store.update(
                        vector_id=row.id,
                        vector=None,
                        payload={**payload, "linked_memory_ids": remaining},
                    )
                else:
                    self.entity_store.delete(vector_id=row.id)
        except Exception:
            if self._strict:
                raise
            logger.warning(
                "Entity store cleanup failed for memories %s", sorted(removed), exc_info=True
            )

    def _link_entities_batch(self, records, search_filters):
        """Link the entities of newly stored memories, one embedding per distinct entity.

        records are (memory_id, text, embedding, payload) tuples already stored.
        An entity record with the same normalized text, or one at least 0.95
        similar, gains the new links; other entities are inserted together. On a
        borrowed handle, or with prepared embeddings, every entity embedding
        comes from _entity_embed and any failure raises; otherwise failures are
        logged and the stored memories stand.
        """
        try:
            all_entities = extract_entities_batch([record[1] for record in records])
            global_entities = {}  # normalized text -> [entity_type, entity_text, memory_ids]
            for idx, record in enumerate(records):
                for entity_type, entity_text in (
                    all_entities[idx] if idx < len(all_entities) else []
                ):
                    key = self._normalize_entity_text(entity_text)
                    if key in global_entities:
                        global_entities[key][2].add(record[0])
                    else:
                        global_entities[key] = [entity_type, entity_text, {record[0]}]
            if not global_entities:
                return

            ordered_keys = list(global_entities)
            entity_texts = [global_entities[key][1] for key in ordered_keys]
            if self._strict or self._planned_embeddings is not None:
                entity_embeddings = [self._entity_embed(text, "add") for text in entity_texts]
            else:
                entity_embeddings = self._embed_entity_texts_tolerantly(entity_texts)

            valid = [
                (i, key) for i, key in enumerate(ordered_keys) if entity_embeddings[i] is not None
            ]
            if not valid:
                return
            valid_vectors = [entity_embeddings[i] for i, _ in valid]
            valid_keys = [key for _, key in valid]
            exact_matches = self._existing_entities_by_text(search_filters)
            existing_matches = self.entity_store.search_batch(
                queries=[global_entities[key][1] for key in valid_keys],
                vectors_list=valid_vectors,
                top_k=1,
                filters=search_filters,
            )

            to_insert_vectors, to_insert_ids, to_insert_payloads = [], [], []
            for j, key in enumerate(valid_keys):
                entity_type, entity_text, memory_ids = global_entities[key]
                matches = existing_matches[j] if j < len(existing_matches) else []
                semantic_match = matches[0] if matches and matches[0].score >= 0.95 else None
                match = exact_matches.get(key) or semantic_match
                if match is None:
                    to_insert_vectors.append(valid_vectors[j])
                    to_insert_ids.append(str(uuid.uuid4()))
                    to_insert_payloads.append(
                        {
                            "data": entity_text,
                            "entity_type": entity_type,
                            "linked_memory_ids": sorted(memory_ids),
                            **search_filters,
                        }
                    )
                    continue
                payload = match.payload or {}
                payload["linked_memory_ids"] = sorted(
                    set(payload.get("linked_memory_ids", [])) | memory_ids
                )
                try:
                    self.entity_store.update(vector_id=match.id, vector=None, payload=payload)
                except Exception:
                    if self._strict:
                        raise
                    logger.warning("Entity update failed for %r", entity_text, exc_info=True)

            if to_insert_vectors:
                self.entity_store.insert(
                    vectors=to_insert_vectors, ids=to_insert_ids, payloads=to_insert_payloads
                )
        except Exception:
            if self._strict:
                raise
            logger.warning("Batch entity linking failed", exc_info=True)

    def _embed_entity_texts_tolerantly(self, entity_texts):
        """Embed entity texts in one batch, falling back to one at a time.

        Returns one vector per text, None where embedding failed (each failure is
        logged). Used only off a borrowed handle, where entity links are best effort.
        """
        try:
            embeddings = list(self.embedding_model.embed_batch(entity_texts, "add"))
        except Exception:
            logger.warning("Batch entity embedding failed; embedding one at a time", exc_info=True)
            embeddings = []
            for text in entity_texts:
                try:
                    embeddings.append(self.embedding_model.embed(text, "add"))
                except Exception:
                    logger.warning("Entity embedding failed for %r", text, exc_info=True)
                    embeddings.append(None)
        if len(embeddings) != len(entity_texts):
            logger.warning(
                "embed_batch returned %d vectors for %d entity texts; missing ones are skipped",
                len(embeddings),
                len(entity_texts),
            )
            embeddings = embeddings[: len(entity_texts)]
            embeddings += [None] * (len(entity_texts) - len(embeddings))
        return embeddings

    def _execute_memory_actions(
        self,
        new_memories_with_actions,
        temp_uuid_mapping,
        new_message_embeddings,
        metadata,
        filters,
    ):
        """Stage 4: apply validated decisions in order through the mutation helpers.

        This is the only stage that writes. ADD stores the fact and links its
        entities; UPDATE and DELETE first re-read their target and refuse one that
        is missing or outside the identity scope; NONE only refreshes a newly
        supplied agent or run id. Pass embeddings for every ADD/UPDATE text to keep
        this stage free of model calls. Returns the changed-memory results; any
        failure is raised unchanged, so a caller-owned transaction can roll back.
        """
        search_filters = self._identity_filters(filters)
        returned_memories = []
        for resp in new_memories_with_actions.get("memory", []):
            action_text = resp["text"]
            event_type = resp["event"]
            target_id = temp_uuid_mapping.get(resp.get("id"))
            if event_type == "ADD":
                if action_text not in new_message_embeddings:
                    new_message_embeddings[action_text] = self.embedding_model.embed(
                        action_text, "add"
                    )
                memory_id = self._create_memory(
                    data=action_text,
                    existing_embeddings=new_message_embeddings,
                    metadata=deepcopy(metadata),
                )
                self._link_entities_for_memory(memory_id, action_text, search_filters)
                returned_memories.append({"id": memory_id, "memory": action_text, "event": "ADD"})
            elif event_type == "UPDATE":
                self._require_live_scoped_target(target_id, search_filters)
                if action_text not in new_message_embeddings:
                    new_message_embeddings[action_text] = self.embedding_model.embed(
                        action_text, "update"
                    )
                self._update_memory(
                    memory_id=target_id,
                    data=action_text,
                    existing_embeddings=new_message_embeddings,
                    metadata=deepcopy(metadata),
                )
                returned_memories.append(
                    {
                        "id": target_id,
                        "memory": action_text,
                        "event": "UPDATE",
                        "previous_memory": resp.get("old_memory"),
                    }
                )
            elif event_type == "DELETE":
                self._require_live_scoped_target(target_id, search_filters)
                self._delete_memory(memory_id=target_id)
                returned_memories.append(
                    {"id": target_id, "memory": action_text, "event": "DELETE"}
                )
            elif metadata.get("agent_id") or metadata.get("run_id"):
                existing_memory = self.vector_store.get(vector_id=target_id)
                if existing_memory is None:
                    raise ValueError(f"Memory {target_id} disappeared before session update")
                _require_scoped_memory(existing_memory, search_filters)
                self.vector_store.update(
                    vector_id=target_id,
                    vector=None,
                    payload=_with_session_ids(existing_memory.payload, metadata),
                )
                logger.info(f"Updated session IDs for memory {target_id}")
            else:
                logger.info("NOOP for Memory.")
        return returned_memories

    def _add_raw_messages(self, messages, metadata):
        """Store each non-system message as written (infer=False), one memory per message.

        A message without a role or content is skipped with a warning; a message
        name becomes the memory's actor_id. Returns the ADD results.
        """
        returned_memories = []
        for message_dict in messages:
            if (
                not isinstance(message_dict, dict)
                or message_dict.get("role") is None
                or message_dict.get("content") is None
            ):
                logger.warning(f"Skipping invalid message format: {message_dict}")
                continue

            if message_dict["role"] == "system":
                continue

            per_msg_meta = deepcopy(metadata)
            per_msg_meta["role"] = message_dict["role"]

            actor_name = message_dict.get("name")
            if actor_name:
                per_msg_meta["actor_id"] = actor_name

            msg_content = message_dict["content"]
            msg_embeddings = self.embedding_model.embed(msg_content, "add")
            mem_id = self._create_memory(msg_content, {msg_content: msg_embeddings}, per_msg_meta)

            returned_memories.append(
                {
                    "id": mem_id,
                    "memory": msg_content,
                    "event": "ADD",
                    "actor_id": actor_name if actor_name else None,
                    "role": message_dict["role"],
                }
            )
        return returned_memories

    def _compute_entity_boosts(self, query_entities, filters):
        """Return {memory_id: boost} from entity records similar to the query's entities.

        Up to eight distinct query entities are embedded in one batch and searched
        in the scoped entity store one after another on the store's own handle,
        never from extra threads. A match at similarity 0.5 or more lends each of
        its linked memories a boost of at most ENTITY_BOOST_WEIGHT, damped for
        entities linked to many memories. A failure is logged and yields no
        boosts, so ranking falls back to the semantic and keyword signals.
        """
        seen = set()
        deduped = []
        for _entity_type, entity_text in query_entities[:8]:
            key = self._normalize_entity_text(entity_text)
            if key and key not in seen:
                seen.add(key)
                deduped.append(entity_text)
        if not deduped:
            return {}

        search_filters = self._identity_filters(filters)
        memory_boosts = {}
        try:
            embeddings = self.embedding_model.embed_batch(deduped, "search")
            if len(embeddings) != len(deduped):
                logger.warning(
                    "embed_batch returned %d vectors for %d texts — skipping entity boost",
                    len(embeddings),
                    len(deduped),
                )
                return memory_boosts
            all_matches = self.entity_store.search_batch(
                queries=deduped, vectors_list=embeddings, top_k=500, filters=search_filters
            )
            for matches in all_matches:
                for match in matches:
                    similarity = match.score if hasattr(match, "score") else 0.0
                    if similarity < 0.5:
                        continue
                    payload = match.payload if hasattr(match, "payload") else {}
                    linked_memory_ids = payload.get("linked_memory_ids", [])
                    if not isinstance(linked_memory_ids, list):
                        continue
                    num_linked = max(len(linked_memory_ids), 1)
                    memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
                    boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
                    for memory_id in linked_memory_ids:
                        if memory_id:
                            memory_key = str(memory_id)
                            memory_boosts[memory_key] = max(
                                memory_boosts.get(memory_key, 0.0), boost
                            )
        except Exception:
            logger.warning("Entity boost computation failed", exc_info=True)
        return memory_boosts

    def _delete_scope(self, filters):
        """Delete every memory within filters, then unlink them from entity records.

        Each deletion writes its own history row. Returns how many memories were
        deleted. Raises VectorStoreError if a listing returns memories that were
        already deleted, instead of reporting a partial deletion as complete.
        """
        deleted_ids = []
        seen_batches = set()
        while True:
            rows = _vector_store_list_rows(
                self.vector_store.list(filters=filters, top_k=DELETE_ALL_BATCH_SIZE)
            )
            if not rows:
                break
            batch_ids = tuple(sorted(str(row.id) for row in rows))
            if batch_ids in seen_batches:
                raise VectorStoreError(
                    f"delete_all listed {len(batch_ids)} memories again after deleting them"
                )
            seen_batches.add(batch_ids)
            for row in rows:
                self._delete_memory(row.id, existing_memory=row, unlink_entities=False)
                deleted_ids.append(str(row.id))
        self._remove_memories_from_entity_store(deleted_ids, filters, scan_scope=True)
        logger.info(f"Deleted {len(deleted_ids)} memories")
        return len(deleted_ids)

    def _reset_stores(self):
        """Drop and recreate the history, vector and entity collections.

        A borrowed handle is reused as is: no connection is opened, committed or
        closed. The entity collection is reset even if this process never used
        it, so entity text derived from the removed memories does not survive.
        """
        self.db.reset()
        self.db.close()
        self.db = DuckDBManager(self.config.history_db_path, connection=self._borrowed_connection)

        if hasattr(self.vector_store, "reset"):
            self.vector_store = VectorStoreFactory.reset(self.vector_store)
        else:
            logger.warning("Vector store does not support reset. Skipping.")
            self.vector_store.delete_col()
            self.vector_store = VectorStoreFactory.create(
                self.config.vector_store.provider, self.config.vector_store.config
            )
        try:
            self.entity_store.reset()
        except Exception:
            if self._strict:
                raise
            logger.warning("Failed to reset entity store", exc_info=True)

    def close(self):
        """Release the connections this client opened; a borrowed handle stays open for its owner.

        Closes the history manager and every store whose provider offers close(),
        so a file-backed database is released for a restart. Safe to call twice.
        """
        for store in (
            getattr(self, "vector_store", None),
            getattr(self, "_entity_store", None),
            getattr(self, "_telemetry_vector_store", None),
        ):
            close_store = getattr(store, "close", None)
            if callable(close_store):
                close_store()
        if getattr(self, "db", None) is not None:
            self.db.close()
            self.db = None

    def _additive_add(self, messages, metadata, filters, prompt=None, sync_type="sync"):
        """Run the maintained ADD-only extraction pipeline (one model call, batch writes).

        Expired memories are not offered to the model and do not count as
        duplicates. Off a borrowed handle a failed batch write falls back to
        single writes as upstream does; on a borrowed handle any failed write
        raises so the caller's transaction rolls back. Returns the ADD results.
        """
        # === V3 PHASED BATCH PIPELINE ===

        # Phase 0: Context gathering
        session_scope = _build_session_scope(filters)
        last_messages = self.db.get_last_messages(session_scope, limit=10)
        parsed_messages = parse_messages(messages)

        # Phase 1: Existing memory retrieval
        search_filters = {
            k: v for k, v in filters.items() if k in ("user_id", "agent_id", "run_id") and v
        }
        query_embedding = self.embedding_model.embed(parsed_messages, "search")
        existing_results = self.vector_store.search(
            query=parsed_messages,
            vectors=query_embedding,
            top_k=10,
            filters=search_filters,
        )
        # An expired memory is gone for its user: it must not absorb or suppress a new fact.
        existing_results = [
            mem
            for mem in existing_results
            if not _payload_is_expired(getattr(mem, "payload", None))
        ]

        # Map UUIDs to integers (anti-hallucination)
        existing_memories = []
        uuid_mapping = {}
        for idx, mem in enumerate(existing_results):
            uuid_mapping[str(idx)] = mem.id
            existing_memories.append({"id": str(idx), "text": mem.payload.get("data", "")})

        # Phase 2: LLM extraction (single call)
        is_agent_scoped = bool(filters.get("agent_id")) and not filters.get("user_id")
        system_prompt = ADDITIVE_EXTRACTION_PROMPT
        if is_agent_scoped:
            system_prompt += AGENT_CONTEXT_SUFFIX

        custom_instr = prompt or self.custom_instructions

        user_prompt = generate_additive_extraction_prompt(
            existing_memories=existing_memories,
            new_messages=parsed_messages,
            last_k_messages=last_messages,
            custom_instructions=custom_instr,
        )

        try:
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            # Re-raise so callers can implement provider fallback / retry.
            # The original silent ``return []`` made upstream callers unable to
            # distinguish "LLM unavailable" (429/5xx/timeout) from "LLM
            # extracted no facts" -- both surfaced as an empty list.
            logger.error(f"LLM extraction failed: {e}")
            raise LLMError(f"LLM extraction failed: {e}") from e

        # Parse response
        try:
            response = remove_code_blocks(response)
            if not response or not response.strip():
                extracted_memories = []
            else:
                try:
                    extracted_memories = json.loads(response, strict=False).get("memory", [])
                except json.JSONDecodeError:
                    extracted_json = extract_json(response)
                    extracted_memories = json.loads(extracted_json, strict=False).get("memory", [])
        except Exception as e:
            # An unreadable reply is a model failure, not "no facts": raise it, as the
            # decision flow does, instead of reporting an empty successful add.
            logger.error(f"Error parsing extraction response: {e}")
            raise LLMError(f"Invalid extraction response: {e}") from e

        if not extracted_memories:
            # Save messages even if nothing extracted
            self.db.save_messages(messages, session_scope)
            return []

        # Phase 3: Batch embed all extracted memory texts
        mem_texts = [m.get("text", "") for m in extracted_memories if m.get("text")]
        try:
            mem_embeddings_list = self.embedding_model.embed_batch(mem_texts, "add")
            embed_map = dict(zip(mem_texts, mem_embeddings_list, strict=True))
        except Exception:
            logger.warning("Batch memory embedding failed; embedding one at a time", exc_info=True)
            embed_map = {}
            for text in mem_texts:
                try:
                    embed_map[text] = self.embedding_model.embed(text, "add")
                except Exception:
                    logger.warning("Failed to embed memory text", exc_info=True)

        # Phase 4: Per-memory CPU processing + Phase 5: Hash dedup
        # Build set of existing hashes for dedup
        existing_hashes = set()
        for mem in existing_results:
            h = mem.payload.get("hash") if hasattr(mem, "payload") and mem.payload else None
            if h:
                existing_hashes.add(h)

        records = []  # (memory_id, text, embedding, payload)
        seen_hashes = set()  # dedup within the current batch
        for mem in extracted_memories:
            text = mem.get("text")
            if not text or text not in embed_map:
                continue

            mem_hash = hashlib.md5(text.encode()).hexdigest()
            if mem_hash in existing_hashes or mem_hash in seen_hashes:
                logger.debug(f"Skipping duplicate memory (hash match): {text[:50]}")
                continue
            seen_hashes.add(mem_hash)

            text_lemmatized = lemmatize_for_bm25(text)

            memory_id = str(uuid.uuid4())
            mem_metadata = deepcopy(metadata)
            mem_metadata["data"] = text
            mem_metadata["text_lemmatized"] = text_lemmatized
            mem_metadata["hash"] = mem_hash
            if "created_at" not in mem_metadata:
                mem_metadata["created_at"] = datetime.now(timezone.utc).isoformat()
            mem_metadata["updated_at"] = mem_metadata["created_at"]
            if mem.get("attributed_to"):
                mem_metadata["attributed_to"] = mem["attributed_to"]

            records.append((memory_id, text, embed_map[text], mem_metadata))

        if not records:
            self.db.save_messages(messages, session_scope)
            return []

        # Phase 6: Batch persist
        all_vectors = [r[2] for r in records]
        all_ids = [r[0] for r in records]
        all_payloads = [r[3] for r in records]

        # Only records confirmed to be stored make it into history, entity
        # links, and the returned results — a record the store rejected must
        # never be reported back as a successful ADD.
        persisted_records = []
        try:
            self.vector_store.insert(
                vectors=all_vectors,
                ids=all_ids,
                payloads=all_payloads,
            )
            persisted_records = records
        except Exception:
            if self._strict:
                raise
            # Fallback: insert one by one
            for rec in records:
                try:
                    self.vector_store.insert(vectors=[rec[2]], ids=[rec[0]], payloads=[rec[3]])
                    persisted_records.append(rec)
                except Exception:
                    logger.error(f"Failed to insert memory {rec[0]}", exc_info=True)

        if not persisted_records:
            self.db.save_messages(messages, session_scope)
            raise VectorStoreError(
                f"Failed to insert any of the {len(records)} extracted memories into the vector store"
            )

        # Batch history
        history_records = [
            {
                "memory_id": r[0],
                "old_memory": None,
                "new_memory": r[1],
                "event": "ADD",
                "created_at": r[3].get("created_at"),
                "is_deleted": 0,
            }
            for r in persisted_records
        ]
        try:
            self.db.batch_add_history(history_records)
        except Exception:
            if self._strict:
                raise
            # Fallback: add one by one
            for hr in history_records:
                try:
                    self.db.add_history(
                        hr["memory_id"],
                        None,
                        hr["new_memory"],
                        "ADD",
                        created_at=hr.get("created_at"),
                    )
                except Exception:
                    logger.error(f"Failed to add history for {hr['memory_id']}", exc_info=True)

        # Phase 7: Batch entity linking
        self._link_entities_batch(persisted_records, search_filters)

        # Phase 8: Save messages + return
        self.db.save_messages(messages, session_scope)

        returned_memories = [
            {"id": r[0], "memory": r[1], "event": "ADD"} for r in persisted_records
        ]

        keys, encoded_ids = process_telemetry_filters(filters)
        capture_event(
            "mem0.add",
            self,
            {
                "version": self.api_version,
                "keys": keys,
                "encoded_ids": encoded_ids,
                "sync_type": sync_type,
            },
        )
        return returned_memories


class Memory(_MemoryCore, MemoryBase):
    """Synchronous Mem0 client on its own stores, or on a caller-owned native DuckDB handle."""

    def __init__(
        self,
        config: MemoryConfig = MemoryConfig(),
        connection: Optional[duckdb.DuckDBPyConnection] = None,
    ):
        """Create a memory on its own DuckDB files, or on a caller-owned native handle.

        With `connection`, the vector store and history share that one borrowed
        handle (the DuckDB vector store provider is required): this Memory never
        commits, rolls back or closes it, and the caller owns the transaction.
        Entity-store maintenance still runs, on that same handle, but never calls
        the embedding model: it embeds only from the set a caller prepared with
        `use_planned_embeddings`, and records any text it needed but was not given
        in `planned_embedding_misses` so the caller can refuse to commit.
        """
        self.config = config
        self._borrowed_connection = connection
        self._planned_embeddings = None
        self.planned_embedding_misses = []
        if connection is not None and self.config.vector_store.provider != "duckdb":
            raise ValueError("A borrowed DuckDB handle requires the duckdb vector store provider")

        self.embedding_model = EmbedderFactory.create(
            self.config.embedder.provider,
            self.config.embedder.config,
            self.config.vector_store.config,
        )
        self.vector_store = VectorStoreFactory.create(
            self.config.vector_store.provider,
            self.config.vector_store.config,
            **({"connection": connection} if connection is not None else {}),
        )
        self.llm = LlmFactory.create(self.config.llm.provider, self.config.llm.config)
        self.db = DuckDBManager(self.config.history_db_path, connection=connection)
        self.collection_name = self.config.vector_store.config.collection_name
        self.api_version = self.config.version
        self.custom_instructions = self.config.custom_instructions

        # Initialize reranker if configured
        self.reranker = None
        if config.reranker:
            self.reranker = RerankerFactory.create(config.reranker.provider, config.reranker.config)

        # Entity store is initialized lazily on first use
        self._entity_store = None

        if MEM0_TELEMETRY:
            # Create telemetry config manually to avoid deepcopy issues with thread locks
            telemetry_config_dict = {}
            if hasattr(self.config.vector_store.config, "model_dump"):
                # For pydantic models
                telemetry_config_dict = self.config.vector_store.config.model_dump()
            else:
                # For other objects, manually copy common attributes
                for attr in [
                    "host",
                    "port",
                    "path",
                    "api_key",
                    "index_name",
                    "dimension",
                    "metric",
                ]:
                    if hasattr(self.config.vector_store.config, attr):
                        telemetry_config_dict[attr] = getattr(self.config.vector_store.config, attr)

            # Override collection name for telemetry
            telemetry_config_dict["collection_name"] = "mem0migrations"

            # Set path for file-based vector stores
            telemetry_config = _safe_deepcopy_config(self.config.vector_store.config)
            if self.config.vector_store.provider in ["faiss", "qdrant"]:
                provider_path = f"migrations_{self.config.vector_store.provider}"
                telemetry_config_dict["path"] = os.path.join(mem0_dir, provider_path)
                os.makedirs(telemetry_config_dict["path"], exist_ok=True)

            # Create the config object using the same class as the original
            telemetry_config = self.config.vector_store.config.__class__(**telemetry_config_dict)
            self._telemetry_vector_store = VectorStoreFactory.create(
                self.config.vector_store.provider, telemetry_config
            )
        if (
            getattr(type(self.vector_store), "keyword_search", None)
            is VectorStoreBase.keyword_search
        ):
            logger.warning(
                "The '%s' vector store does not support keyword search. "
                "Hybrid (BM25) scoring will be disabled and search will use "
                "semantic similarity only. To enable hybrid search, switch to a "
                "store with keyword_search support (e.g. qdrant, elasticsearch, pgvector).",
                self.config.vector_store.provider,
            )

        capture_event("mem0.init", self, {"sync_type": "sync"})

    @property
    def project(self):
        """Return the OSS project handle, which refuses platform-only project updates."""
        return _OSSProject()

    @classmethod
    def from_config(cls, config_dict: Dict[str, Any]):
        """Build a client from a config dict; raises pydantic ValidationError for a bad config."""
        try:
            config = MemoryConfig(**config_dict)
        except ValidationError as e:
            logger.error(f"Configuration validation error: {e}")
            raise
        return cls(config)

    def add(
        self,
        messages,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[Any] = None,
        expiration_date: Optional[Any] = None,
        infer: bool = True,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
    ):
        """
        Create a new memory.

        Adds new memories scoped to a single session id (e.g. `user_id`, `agent_id`, or `run_id`). One of those ids is required.

        Args:
            messages (str or List[Dict[str, str]]): The message content or list of messages
                (e.g., `[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]`)
                to be processed and stored.
            user_id (str, optional): ID of the user creating the memory. Defaults to None.
            agent_id (str, optional): ID of the agent creating the memory. Defaults to None.
            run_id (str, optional): ID of the run creating the memory. Defaults to None.
            metadata (dict, optional): Metadata to store with the memory. Defaults to None.
            timestamp (Any, optional): Platform-only temporal parameter. Not supported in OSS.
            expiration_date (Any, optional): Date in YYYY-MM-DD format. Expired memories are hidden
                from search and get_all unless show_expired is True.
            infer (bool, optional): If True (default), an LLM is used to extract key facts from
                'messages' and decide whether to add, update, or delete related memories.
                If False, 'messages' are added as raw memories directly.
            memory_type (str, optional): Specifies the type of memory. Currently, only
                `MemoryType.PROCEDURAL.value` ("procedural_memory") is explicitly handled for
                creating procedural memories (typically requires 'agent_id'). Otherwise, memories
                are treated as general conversational/factual memories.
            prompt (str, optional): Prompt to use for the memory creation. Defaults to None.

        Note:
            `search()` and `get_all()` scope queries via `filters={"user_id": "...", "agent_id": "...", "run_id": "..."}` —
            they reject top-level `user_id`/`agent_id`/`run_id` arguments. `add()` accepts them top-level, but passing
            the same arguments to `search()`/`get_all()` raises a `ValueError`; use the `filters` form there instead.


        Returns:
            dict: A dictionary containing the result of the memory addition operation, typically
                  including a list of memory items affected (added, updated) under a "results" key.
                  Example for v1.1+: `{"results": [{"id": "...", "memory": "...", "event": "ADD"}]}`

        Raises:
            Mem0ValidationError: If input validation fails (invalid memory_type, messages format, etc.).
            VectorStoreError: If vector store operations fail.
            EmbeddingError: If embedding generation fails.
            LLMError: If LLM operations fail.
            DatabaseError: If database operations fail.
        """
        if timestamp is not None:
            raise ValueError(get_temporal_feature_error_message("sync", "add", "timestamp"))

        normalized_expiration_date = _normalize_expiration_date(expiration_date)
        temporal_usage_notice = detect_temporal_usage_from_metadata(metadata)
        processed_metadata, effective_filters = _build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            input_metadata=metadata,
        )
        if normalized_expiration_date is not None:
            processed_metadata["expiration_date"] = normalized_expiration_date

        if memory_type is not None and memory_type != MemoryType.PROCEDURAL.value:
            raise Mem0ValidationError(
                message=f"Invalid 'memory_type'. Please pass {MemoryType.PROCEDURAL.value} to create procedural memories.",
                error_code="VALIDATION_002",
                details={"provided_type": memory_type, "valid_type": MemoryType.PROCEDURAL.value},
                suggestion=f"Use '{MemoryType.PROCEDURAL.value}' to create procedural memories.",
            )

        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        elif isinstance(messages, dict):
            messages = [messages]

        elif not isinstance(messages, list):
            raise Mem0ValidationError(
                message="messages must be str, dict, or list[dict]",
                error_code="VALIDATION_003",
                details={
                    "provided_type": type(messages).__name__,
                    "valid_types": ["str", "dict", "list[dict]"],
                },
                suggestion="Convert your input to a string, dictionary, or list of dictionaries.",
            )

        # TODO: Remove procedural-memory support in a future breaking release.
        # Remove memory_type and its helpers from Memory and AsyncMemory together.
        if agent_id is not None and memory_type == MemoryType.PROCEDURAL.value:
            results = self._create_procedural_memory(
                messages, metadata=processed_metadata, prompt=prompt
            )
            scale_threshold_notice = detect_scale_threshold_from_add_result(self, results)
            if temporal_usage_notice:
                display_temporal_usage_notice(self, "sync", "add", *temporal_usage_notice)
            elif scale_threshold_notice:
                display_scale_threshold_notice(self, "sync", "add", *scale_threshold_notice)
            else:
                display_first_run_notice(self, "sync", "add")
            return results

        if self.config.llm.config.get("enable_vision"):
            messages = parse_vision_messages(
                messages, self.llm, self.config.llm.config.get("vision_details")
            )
        else:
            messages = parse_vision_messages(messages)

        vector_store_result = self._add_to_vector_store(
            messages, processed_metadata, effective_filters, infer, prompt=prompt
        )
        scale_threshold_notice = detect_scale_threshold_from_add_result(self, vector_store_result)
        if temporal_usage_notice:
            display_temporal_usage_notice(self, "sync", "add", *temporal_usage_notice)
        elif scale_threshold_notice:
            display_scale_threshold_notice(self, "sync", "add", *scale_threshold_notice)
        else:
            display_first_run_notice(self, "sync", "add")
        return {"results": vector_store_result}

    def _add_to_vector_store(self, messages, metadata, filters, infer, prompt=None):
        """Store messages as memories: raw (infer=False), the restored decision flow, or ADD-only.

        The restored ADD/UPDATE/DELETE/NONE flow runs when the config enables it;
        otherwise Mem0's ADD-only extraction pipeline runs. Returns the results list.
        """
        if not infer:
            return self._add_raw_messages(messages, metadata)
        if self.config.enable_update_delete_branches:
            return self._add_with_memory_actions(messages, metadata, filters, prompt=prompt)
        return self._additive_add(messages, metadata, filters, prompt=prompt, sync_type="sync")

    def get(self, memory_id):
        """
        Retrieve a memory by ID.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory.
        """
        capture_event("mem0.get", self, {"memory_id": memory_id, "sync_type": "sync"})
        memory = self.vector_store.get(vector_id=memory_id)
        if not memory:
            display_first_run_notice(self, "sync", "get")
            return None

        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
            "attributed_to",
            "expiration_date",
        ]

        core_and_promoted_keys = {
            "data",
            "hash",
            "created_at",
            "updated_at",
            "id",
            "text_lemmatized",
            "attributed_to",
            *promoted_payload_keys,
        }

        result_item = MemoryItem(
            id=memory.id,
            memory=memory.payload.get("data", ""),
            hash=memory.payload.get("hash"),
            created_at=memory.payload.get("created_at"),
            updated_at=memory.payload.get("updated_at"),
        ).model_dump()

        for key in promoted_payload_keys:
            if key in memory.payload:
                result_item[key] = memory.payload[key]

        additional_metadata = {
            k: v for k, v in memory.payload.items() if k not in core_and_promoted_keys
        }
        if additional_metadata:
            result_item["metadata"] = additional_metadata

        display_first_run_notice(self, "sync", "get")
        return result_item

    def get_all(
        self,
        *,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 20,
        show_expired: bool = False,
        **kwargs,
    ):
        """
        List all memories.

        Args:
            filters (dict): Filter dict containing entity IDs and optional metadata filters.
                Must contain at least one of: user_id, agent_id, run_id.
                Example: filters={"user_id": "u1", "agent_id": "a1"}
            top_k (int, optional): The maximum number of memories to return. Defaults to 20.
            show_expired (bool, optional): Include expired memories. Defaults to False.

        Returns:
            dict: A dictionary containing a list of memories under the "results" key.
                  Example for v1.1+: `{"results": [{"id": "...", "memory": "...", ...}]}`

        Raises:
            ValueError: If filters doesn't contain at least one of user_id, agent_id, run_id,
                or if top_k is invalid.
        """
        # Reject top-level entity params - must use filters instead
        _reject_top_level_entity_params(kwargs, "get_all")

        # Validate top_k
        _validate_search_params(top_k=top_k)

        # Validate and trim entity IDs in filters
        effective_filters = dict(filters) if filters else {}
        if "user_id" in effective_filters:
            effective_filters["user_id"] = _validate_and_trim_entity_id(
                effective_filters["user_id"], "user_id"
            )
        if "agent_id" in effective_filters:
            effective_filters["agent_id"] = _validate_and_trim_entity_id(
                effective_filters["agent_id"], "agent_id"
            )
        if "run_id" in effective_filters:
            effective_filters["run_id"] = _validate_and_trim_entity_id(
                effective_filters["run_id"], "run_id"
            )

        # Validate filters contains at least one entity ID
        if not any(key in effective_filters for key in ("user_id", "agent_id", "run_id")):
            raise ValueError(
                "filters must contain at least one of: user_id, agent_id, run_id. "
                "Example: filters={'user_id': 'u1'}"
            )

        limit = top_k
        fetch_limit = limit if show_expired else max(limit * 4, 60)
        scale_threshold_notice = detect_scale_threshold_from_top_k(top_k)

        keys, encoded_ids = process_telemetry_filters(effective_filters)
        capture_event(
            "mem0.get_all",
            self,
            {"limit": limit, "keys": keys, "encoded_ids": encoded_ids, "sync_type": "sync"},
        )

        all_memories_result = self._get_all_from_vector_store(
            effective_filters, fetch_limit, show_expired, limit
        )

        if scale_threshold_notice:
            display_scale_threshold_notice(self, "sync", "get_all", *scale_threshold_notice)
        else:
            display_first_run_notice(self, "sync", "get_all")
        return {"results": all_memories_result}

    def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        threshold: float = 0.1,
        rerank: bool = False,
        explain: bool = False,
        reference_date: Optional[Any] = None,
        show_expired: bool = False,
        **kwargs,
    ):
        """
        Searches for memories based on a query.

        Args:
            query (str): Query to search for.
            top_k (int, optional): Maximum number of results to return. Defaults to 20.
            filters (dict): Filter dict containing entity IDs and optional metadata filters.
                Must contain at least one of: user_id, agent_id, run_id.
                Example: filters={"user_id": "u1", "agent_id": "a1"}

                Enhanced metadata filtering with operators:
                - {"key": "value"} - exact match
                - {"key": {"eq": "value"}} - equals
                - {"key": {"ne": "value"}} - not equals
                - {"key": {"in": ["val1", "val2"]}} - in list
                - {"key": {"nin": ["val1", "val2"]}} - not in list
                - {"key": {"gt": 10}} - greater than
                - {"key": {"gte": 10}} - greater than or equal
                - {"key": {"lt": 10}} - less than
                - {"key": {"lte": 10}} - less than or equal
                - {"key": {"contains": "text"}} - contains text
                - {"key": {"icontains": "text"}} - case-insensitive contains
                - {"key": "*"} - wildcard match (any value)
                - {"AND": [filter1, filter2]} - logical AND
                - {"OR": [filter1, filter2]} - logical OR
                - {"NOT": [filter1]} - logical NOT
            threshold (float, optional): Minimum score for a memory to be included. Defaults to 0.1.
            rerank (bool, optional): Whether to rerank results. Defaults to False.
            explain (bool, optional): Whether to include score_details for each result. Defaults to False.
            reference_date (Any, optional): Platform-only temporal parameter. Not supported in OSS.
            show_expired (bool, optional): Include expired memories. Defaults to False.

        Returns:
            dict: A dictionary containing the search results under a "results" key.
                  Example for v1.1+: `{"results": [{"id": "...", "memory": "...", "score": 0.8, ...}]}`

        Raises:
            ValueError: If filters doesn't contain at least one of user_id, agent_id, run_id,
                or if threshold/top_k values are invalid.
        """
        if reference_date is not None:
            raise ValueError(get_temporal_feature_error_message("sync", "search", "reference_date"))

        # Reject top-level entity params - must use filters instead
        _reject_top_level_entity_params(kwargs, "search")

        # Validate search parameters (before applying defaults)
        _validate_search_params(threshold=threshold, top_k=top_k)
        query = _validate_and_trim_search_query(query)
        temporal_usage_notice = detect_temporal_usage_from_search(query, filters)

        # Validate and trim entity IDs in filters
        effective_filters = filters.copy() if filters else {}
        if "user_id" in effective_filters:
            effective_filters["user_id"] = _validate_and_trim_entity_id(
                effective_filters["user_id"], "user_id"
            )
        if "agent_id" in effective_filters:
            effective_filters["agent_id"] = _validate_and_trim_entity_id(
                effective_filters["agent_id"], "agent_id"
            )
        if "run_id" in effective_filters:
            effective_filters["run_id"] = _validate_and_trim_entity_id(
                effective_filters["run_id"], "run_id"
            )
        if not any(key in effective_filters for key in ("user_id", "agent_id", "run_id")):
            raise ValueError(
                "filters must contain at least one of: user_id, agent_id, run_id. "
                "Example: filters={'user_id': 'u1'}"
            )

        limit = top_k
        scale_threshold_notice = detect_scale_threshold_from_top_k(top_k)

        # Apply enhanced metadata filtering if advanced operators are detected
        if self._has_advanced_operators(effective_filters):
            processed_filters = self._process_metadata_filters(effective_filters)
            # Remove logical/operator keys that have been reprocessed
            for logical_key in ("AND", "OR", "NOT"):
                effective_filters.pop(logical_key, None)
            for fk in list(effective_filters.keys()):
                if fk not in ("AND", "OR", "NOT", "user_id", "agent_id", "run_id") and isinstance(
                    effective_filters.get(fk), dict
                ):
                    effective_filters.pop(fk, None)
            effective_filters.update(processed_filters)

        keys, encoded_ids = process_telemetry_filters(effective_filters)
        capture_event(
            "mem0.search",
            self,
            {
                "limit": limit,
                "version": self.api_version,
                "keys": keys,
                "encoded_ids": encoded_ids,
                "sync_type": "sync",
                "threshold": threshold,
                "explain": explain,
                "advanced_filters": bool(filters and self._has_advanced_operators(filters)),
            },
        )

        search_start = time.perf_counter()
        original_memories = self._search_vector_store(
            query, effective_filters, limit, threshold, explain=explain, show_expired=show_expired
        )
        search_elapsed_seconds = time.perf_counter() - search_start

        # Apply reranking if enabled and reranker is available
        if rerank and self.reranker and original_memories:
            try:
                reranked_memories = self.reranker.rerank(query, original_memories, limit)
                original_memories = reranked_memories
            except Exception:
                logger.warning("Reranking failed, using original results", exc_info=True)

        if temporal_usage_notice:
            display_temporal_usage_notice(self, "sync", "search", *temporal_usage_notice)
        elif scale_threshold_notice:
            display_scale_threshold_notice(self, "sync", "search", *scale_threshold_notice)
        elif search_elapsed_seconds > PERFORMANCE_SLOW_QUERY_THRESHOLD_SECONDS:
            display_performance_slow_query_notice(
                self,
                "sync",
                "search",
                search_elapsed_seconds,
                top_k,
                len(original_memories),
            )
        else:
            display_first_run_notice(self, "sync", "search")
        return {"results": original_memories}

    def update(
        self,
        memory_id,
        text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expiration_date: Any = _UNSET,
        data: Optional[str] = None,
    ):
        """
        Update a memory by ID.

        Args:
            memory_id (str): ID of the memory to update.
            text (str, optional): New content to update the memory with.
            metadata (dict, optional): Metadata to update with the memory. Defaults to None.
                ``user_id``/``agent_id``/``run_id``/``actor_id`` are ignored here - they are
                immutable after creation.
            expiration_date (Any, optional): Date in YYYY-MM-DD format, or None to clear it.
            data (str, optional): Deprecated alias for ``text``. Will be removed in the next
                major release; use ``text`` instead.

        Returns:
            dict: Success message indicating the memory was updated.

        Example:
            >>> m.update(memory_id="mem_123", text="Likes to play tennis on weekends")
            {'message': 'Memory updated successfully!'}
        """
        capture_event("mem0.update", self, {"memory_id": memory_id, "sync_type": "sync"})

        if data is not None:
            logger.warning(
                "The `data` argument to update() is deprecated and will be removed in the "
                "next major release. Use `text` instead."
            )
            if text is None:
                text = data

        if text is None and metadata is None and expiration_date is _UNSET:
            raise ValueError("At least one of text, metadata, or expiration_date must be provided.")

        update_metadata = deepcopy(metadata) if metadata is not None else None
        if expiration_date is not _UNSET:
            update_metadata = update_metadata or {}
            update_metadata["expiration_date"] = _normalize_expiration_date(expiration_date)

        existing_embeddings = {}
        if text is not None:
            existing_embeddings[text] = self.embedding_model.embed(text, "update")

        self._update_memory(memory_id, text, existing_embeddings, update_metadata)
        display_first_run_notice(self, "sync", "update")
        return {"message": "Memory updated successfully!"}

    def delete(self, memory_id):
        """
        Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """
        capture_event("mem0.delete", self, {"memory_id": memory_id, "sync_type": "sync"})

        existing_memory = self.vector_store.get(vector_id=memory_id)
        if existing_memory is None:
            raise ValueError(f"Memory with id {memory_id} not found")

        self._delete_memory(memory_id, existing_memory)
        decay_usage_notice = detect_decay_usage_from_delete()
        if decay_usage_notice:
            display_decay_usage_notice(self, "sync", "delete", *decay_usage_notice)
        else:
            display_first_run_notice(self, "sync", "delete")
        return {"message": "Memory deleted successfully!"}

    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        """
        Delete all memories.

        Args:
            user_id (str, optional): ID of the user to delete memories for. Defaults to None.
            agent_id (str, optional): ID of the agent to delete memories for. Defaults to None.
            run_id (str, optional): ID of the run to delete memories for. Defaults to None.
        """
        user_id = _validate_and_trim_entity_id(user_id, "user_id")
        agent_id = _validate_and_trim_entity_id(agent_id, "agent_id")
        run_id = _validate_and_trim_entity_id(run_id, "run_id")

        filters: Dict[str, Any] = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id

        if not filters:
            raise ValueError(
                "At least one filter is required to delete all memories. If you want to delete all memories, use the `reset()` method."
            )

        keys, encoded_ids = process_telemetry_filters(filters)
        capture_event(
            "mem0.delete_all", self, {"keys": keys, "encoded_ids": encoded_ids, "sync_type": "sync"}
        )
        # Keep listing after each batch is deleted: most vector stores cap list().
        deleted_count = self._delete_scope(filters)

        decay_usage_notice = detect_decay_usage_from_delete_all(deleted_count)
        if decay_usage_notice:
            display_decay_usage_notice(self, "sync", "delete_all", *decay_usage_notice)
        else:
            display_first_run_notice(self, "sync", "delete_all")
        return {"message": "Memories deleted successfully!"}

    def history(self, memory_id):
        """
        Get the history of changes for a memory by ID.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory.
        """
        capture_event("mem0.history", self, {"memory_id": memory_id, "sync_type": "sync"})
        history = self.db.get_history(memory_id)
        display_first_run_notice(self, "sync", "history")
        return history

    def _create_procedural_memory(self, messages, metadata=None, prompt=None):
        """Create a procedural memory.

        Args:
            messages (list): List of messages to create a procedural memory from.
            metadata (dict): Metadata to create a procedural memory from.
            prompt (str, optional): Prompt to use for the procedural memory creation. Defaults to None.
        """
        logger.info("Creating procedural memory")

        parsed_messages = [
            {"role": "system", "content": prompt or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
            *messages,
            {
                "role": "user",
                "content": "Create procedural memory of the above conversation.",
            },
        ]

        try:
            procedural_memory = self.llm.generate_response(messages=parsed_messages)
            procedural_memory = remove_code_blocks(procedural_memory)
        except Exception as e:
            logger.error(f"Error generating procedural memory summary: {e}")
            raise

        if not procedural_memory:
            raise ValueError(
                "The LLM returned no content for the procedural memory summary. "
                "The model may have declined the request or returned an empty response."
            )

        if metadata is None:
            raise ValueError("Metadata cannot be done for procedural memory.")

        metadata = {**metadata, "memory_type": MemoryType.PROCEDURAL.value}
        embeddings = self.embedding_model.embed(procedural_memory, memory_action="add")
        memory_id = self._create_memory(
            procedural_memory, {procedural_memory: embeddings}, metadata=metadata
        )
        capture_event(
            "mem0._create_procedural_memory", self, {"memory_id": memory_id, "sync_type": "sync"}
        )

        result = {"results": [{"id": memory_id, "memory": procedural_memory, "event": "ADD"}]}

        return result

    def reset(self):
        """Reset the memory store.

        It does so by:
            Deleting and recreating the vector and entity collections
            Resetting the history database
        A borrowed handle is reused; no connection is opened or closed.
        """
        logger.warning("Resetting all memories")
        self._reset_stores()
        capture_event("mem0.reset", self, {"sync_type": "sync"})
        display_first_run_notice(self, "sync", "reset")

    def __enter__(self):
        """Return this client for use in a with-block; close() runs on exit."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close this client's own connections and let any exception propagate."""
        self.close()
        return False

    def chat(self, query):
        """Refuse: chat is not part of the OSS memory client."""
        raise NotImplementedError("Chat function not implemented yet.")


class AsyncMemory(_MemoryCore, MemoryBase):
    """Asynchronous Mem0 client; its storage and model steps run the shared core in worker threads."""

    def __init__(self, config: MemoryConfig = MemoryConfig()):
        """Create an async memory on its own configured stores.

        Its private steps are the shared synchronous core, run in worker threads.
        """
        self.config = config
        self._borrowed_connection = None
        self._planned_embeddings = None
        self.planned_embedding_misses = []

        self.embedding_model = EmbedderFactory.create(
            self.config.embedder.provider,
            self.config.embedder.config,
            self.config.vector_store.config,
        )
        self.vector_store = VectorStoreFactory.create(
            self.config.vector_store.provider, self.config.vector_store.config
        )
        self.llm = LlmFactory.create(self.config.llm.provider, self.config.llm.config)
        self.db = DuckDBManager(self.config.history_db_path)
        self.collection_name = self.config.vector_store.config.collection_name
        self.api_version = self.config.version
        self.custom_instructions = self.config.custom_instructions
        self._entity_store = None

        # Initialize reranker if configured
        self.reranker = None
        if config.reranker:
            self.reranker = RerankerFactory.create(config.reranker.provider, config.reranker.config)

        if MEM0_TELEMETRY:
            telemetry_config = _safe_deepcopy_config(self.config.vector_store.config)
            telemetry_config.collection_name = "mem0migrations"
            if self.config.vector_store.provider in ["faiss", "qdrant"]:
                provider_path = f"migrations_{self.config.vector_store.provider}"
                telemetry_config.path = os.path.join(mem0_dir, provider_path)
                os.makedirs(telemetry_config.path, exist_ok=True)
            self._telemetry_vector_store = VectorStoreFactory.create(
                self.config.vector_store.provider, telemetry_config
            )

        if (
            getattr(type(self.vector_store), "keyword_search", None)
            is VectorStoreBase.keyword_search
        ):
            logger.warning(
                "The '%s' vector store does not support keyword search. "
                "Hybrid (BM25) scoring will be disabled and search will use "
                "semantic similarity only. To enable hybrid search, switch to a "
                "store with keyword_search support (e.g. qdrant, elasticsearch, pgvector).",
                self.config.vector_store.provider,
            )

        capture_event("mem0.init", self, {"sync_type": "async"})

    @property
    def project(self):
        """Return the async OSS project handle, which refuses platform-only project updates."""
        return _AsyncOSSProject()

    @classmethod
    def from_config(cls, config_dict: Dict[str, Any]):
        """Build a client from a config dict; raises pydantic ValidationError for a bad config."""
        try:
            config = MemoryConfig(**config_dict)
        except ValidationError as e:
            logger.error(f"Configuration validation error: {e}")
            raise
        return cls(config)

    async def add(
        self,
        messages,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[Any] = None,
        expiration_date: Optional[Any] = None,
        infer: bool = True,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        llm=None,
    ):
        """
        Create a new memory asynchronously.

        Args:
            messages (str or List[Dict[str, str]]): Messages to store in the memory.
            user_id (str, optional): ID of the user creating the memory.
            agent_id (str, optional): ID of the agent creating the memory. Defaults to None.
            run_id (str, optional): ID of the run creating the memory. Defaults to None.
            metadata (dict, optional): Metadata to store with the memory. Defaults to None.
            timestamp (Any, optional): Platform-only temporal parameter. Not supported in OSS.
            expiration_date (Any, optional): Date in YYYY-MM-DD format. Expired memories are hidden
                from search and get_all unless show_expired is True.
            infer (bool, optional): Whether to infer the memories. Defaults to True.
            memory_type (str, optional): Type of memory to create. Defaults to None.
                                         Pass "procedural_memory" to create procedural memories.
            prompt (str, optional): Prompt to use for the memory creation. Defaults to None.
            llm (BaseChatModel, optional): LLM class to use for generating procedural memories. Defaults to None. Useful when user is using LangChain ChatModel.

        Note:
            `search()` and `get_all()` scope queries via `filters={"user_id": "...", "agent_id": "...", "run_id": "..."}` —
            they reject top-level `user_id`/`agent_id`/`run_id` arguments. `add()` accepts them top-level, but passing
            the same arguments to `search()`/`get_all()` raises a `ValueError`; use the `filters` form there instead.

        Returns:
            dict: A dictionary containing the result of the memory addition operation.
        """
        if timestamp is not None:
            raise ValueError(
                await get_temporal_feature_error_message_async("async", "add", "timestamp")
            )

        normalized_expiration_date = _normalize_expiration_date(expiration_date)
        temporal_usage_notice = detect_temporal_usage_from_metadata(metadata)
        processed_metadata, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, input_metadata=metadata
        )
        if normalized_expiration_date is not None:
            processed_metadata["expiration_date"] = normalized_expiration_date

        if memory_type is not None and memory_type != MemoryType.PROCEDURAL.value:
            raise ValueError(
                f"Invalid 'memory_type'. Please pass {MemoryType.PROCEDURAL.value} to create procedural memories."
            )

        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        elif isinstance(messages, dict):
            messages = [messages]

        elif not isinstance(messages, list):
            raise Mem0ValidationError(
                message="messages must be str, dict, or list[dict]",
                error_code="VALIDATION_003",
                details={
                    "provided_type": type(messages).__name__,
                    "valid_types": ["str", "dict", "list[dict]"],
                },
                suggestion="Convert your input to a string, dictionary, or list of dictionaries.",
            )

        # TODO: Remove procedural-memory support in a future breaking release.
        # Remove memory_type and its helpers from Memory and AsyncMemory together.
        if agent_id is not None and memory_type == MemoryType.PROCEDURAL.value:
            results = await self._create_procedural_memory(
                messages, metadata=processed_metadata, prompt=prompt, llm=llm
            )
            scale_threshold_notice = await asyncio.to_thread(
                detect_scale_threshold_from_add_result, self, results
            )
            if temporal_usage_notice:
                await display_temporal_usage_notice_async(
                    self, "async", "add", *temporal_usage_notice
                )
            elif scale_threshold_notice:
                await display_scale_threshold_notice_async(
                    self, "async", "add", *scale_threshold_notice
                )
            else:
                await display_first_run_notice_async(self, "async", "add")
            return results

        if self.config.llm.config.get("enable_vision"):
            messages = parse_vision_messages(
                messages, self.llm, self.config.llm.config.get("vision_details")
            )
        else:
            messages = parse_vision_messages(messages)

        vector_store_result = await self._add_to_vector_store(
            messages, processed_metadata, effective_filters, infer, prompt=prompt
        )
        scale_threshold_notice = await asyncio.to_thread(
            detect_scale_threshold_from_add_result, self, vector_store_result
        )
        if temporal_usage_notice:
            await display_temporal_usage_notice_async(self, "async", "add", *temporal_usage_notice)
        elif scale_threshold_notice:
            await display_scale_threshold_notice_async(
                self, "async", "add", *scale_threshold_notice
            )
        else:
            await display_first_run_notice_async(self, "async", "add")
        return {"results": vector_store_result}

    async def _add_to_vector_store(
        self,
        messages: list,
        metadata: dict,
        effective_filters: dict,
        infer: bool,
        prompt: Optional[str] = None,
    ):
        """Store messages through the shared core in a worker thread; see Memory._add_to_vector_store."""
        if not infer:
            return await asyncio.to_thread(self._add_raw_messages, messages, metadata)
        if self.config.enable_update_delete_branches:
            return await asyncio.to_thread(
                self._add_with_memory_actions, messages, metadata, effective_filters, prompt
            )
        return await asyncio.to_thread(
            self._additive_add, messages, metadata, effective_filters, prompt, "async"
        )

    async def get(self, memory_id):
        """
        Retrieve a memory by ID asynchronously.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory.
        """
        capture_event("mem0.get", self, {"memory_id": memory_id, "sync_type": "async"})
        memory = await asyncio.to_thread(self.vector_store.get, vector_id=memory_id)
        if not memory:
            await display_first_run_notice_async(self, "async", "get")
            return None

        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
            "attributed_to",
            "expiration_date",
        ]

        core_and_promoted_keys = {
            "data",
            "hash",
            "created_at",
            "updated_at",
            "id",
            "text_lemmatized",
            "attributed_to",
            *promoted_payload_keys,
        }

        result_item = MemoryItem(
            id=memory.id,
            memory=memory.payload.get("data", ""),
            hash=memory.payload.get("hash"),
            created_at=memory.payload.get("created_at"),
            updated_at=memory.payload.get("updated_at"),
        ).model_dump()

        for key in promoted_payload_keys:
            if key in memory.payload:
                result_item[key] = memory.payload[key]

        additional_metadata = {
            k: v for k, v in memory.payload.items() if k not in core_and_promoted_keys
        }
        if additional_metadata:
            result_item["metadata"] = additional_metadata

        await display_first_run_notice_async(self, "async", "get")
        return result_item

    async def get_all(
        self,
        *,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 20,
        show_expired: bool = False,
        **kwargs,
    ):
        """
        List all memories.

        Args:
            filters (dict): Filter dict containing entity IDs and optional metadata filters.
                Must contain at least one of: user_id, agent_id, run_id.
                Example: filters={"user_id": "u1", "agent_id": "a1"}
            top_k (int, optional): The maximum number of memories to return. Defaults to 20.
            show_expired (bool, optional): Include expired memories. Defaults to False.

        Returns:
            dict: A dictionary containing a list of memories under the "results" key.
                  Example for v1.1+: `{"results": [{"id": "...", "memory": "...", ...}]}`

        Raises:
            ValueError: If filters doesn't contain at least one of user_id, agent_id, run_id,
                or if top_k is invalid.
        """
        # Reject top-level entity params - must use filters instead
        _reject_top_level_entity_params(kwargs, "get_all")

        # Validate top_k
        _validate_search_params(top_k=top_k)

        # Validate and trim entity IDs in filters
        effective_filters = dict(filters) if filters else {}
        if "user_id" in effective_filters:
            effective_filters["user_id"] = _validate_and_trim_entity_id(
                effective_filters["user_id"], "user_id"
            )
        if "agent_id" in effective_filters:
            effective_filters["agent_id"] = _validate_and_trim_entity_id(
                effective_filters["agent_id"], "agent_id"
            )
        if "run_id" in effective_filters:
            effective_filters["run_id"] = _validate_and_trim_entity_id(
                effective_filters["run_id"], "run_id"
            )

        # Validate filters contains at least one entity ID
        if not any(key in effective_filters for key in ("user_id", "agent_id", "run_id")):
            raise ValueError(
                "filters must contain at least one of: user_id, agent_id, run_id. "
                "Example: filters={'user_id': 'u1'}"
            )

        limit = top_k
        fetch_limit = limit if show_expired else max(limit * 4, 60)
        scale_threshold_notice = detect_scale_threshold_from_top_k(top_k)

        keys, encoded_ids = process_telemetry_filters(effective_filters)
        capture_event(
            "mem0.get_all",
            self,
            {"limit": limit, "keys": keys, "encoded_ids": encoded_ids, "sync_type": "async"},
        )

        all_memories_result = await asyncio.to_thread(
            self._get_all_from_vector_store, effective_filters, fetch_limit, show_expired, limit
        )

        if scale_threshold_notice:
            await display_scale_threshold_notice_async(
                self, "async", "get_all", *scale_threshold_notice
            )
        else:
            await display_first_run_notice_async(self, "async", "get_all")
        return {"results": all_memories_result}

    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        threshold: float = 0.1,
        rerank: bool = False,
        explain: bool = False,
        reference_date: Optional[Any] = None,
        show_expired: bool = False,
        **kwargs,
    ):
        """
        Searches for memories based on a query.

        Args:
            query (str): Query to search for.
            top_k (int, optional): Maximum number of results to return. Defaults to 20.
            filters (dict): Filter dict containing entity IDs and optional metadata filters.
                Must contain at least one of: user_id, agent_id, run_id.
                Example: filters={"user_id": "u1", "agent_id": "a1"}

                Enhanced metadata filtering with operators:
                - {"key": "value"} - exact match
                - {"key": {"eq": "value"}} - equals
                - {"key": {"ne": "value"}} - not equals
                - {"key": {"in": ["val1", "val2"]}} - in list
                - {"key": {"nin": ["val1", "val2"]}} - not in list
                - {"key": {"gt": 10}} - greater than
                - {"key": {"gte": 10}} - greater than or equal
                - {"key": {"lt": 10}} - less than
                - {"key": {"lte": 10}} - less than or equal
                - {"key": {"contains": "text"}} - contains text
                - {"key": {"icontains": "text"}} - case-insensitive contains
                - {"key": "*"} - wildcard match (any value)
                - {"AND": [filter1, filter2]} - logical AND
                - {"OR": [filter1, filter2]} - logical OR
                - {"NOT": [filter1]} - logical NOT
            threshold (float, optional): Minimum score for a memory to be included. Defaults to 0.1.
            rerank (bool, optional): Whether to rerank results. Defaults to False.
            explain (bool, optional): Whether to include score_details for each result. Defaults to False.
            reference_date (Any, optional): Platform-only temporal parameter. Not supported in OSS.
            show_expired (bool, optional): Include expired memories. Defaults to False.

        Returns:
            dict: A dictionary containing the search results under a "results" key.
                  Example for v1.1+: `{"results": [{"id": "...", "memory": "...", "score": 0.8, ...}]}`

        Raises:
            ValueError: If filters doesn't contain at least one of user_id, agent_id, run_id,
                or if threshold/top_k values are invalid.
        """
        if reference_date is not None:
            raise ValueError(
                await get_temporal_feature_error_message_async("async", "search", "reference_date")
            )

        # Reject top-level entity params - must use filters instead
        _reject_top_level_entity_params(kwargs, "search")

        # Validate search parameters (before applying defaults)
        _validate_search_params(threshold=threshold, top_k=top_k)
        query = _validate_and_trim_search_query(query)
        temporal_usage_notice = detect_temporal_usage_from_search(query, filters)

        # Validate and trim entity IDs in filters
        effective_filters = filters.copy() if filters else {}
        if "user_id" in effective_filters:
            effective_filters["user_id"] = _validate_and_trim_entity_id(
                effective_filters["user_id"], "user_id"
            )
        if "agent_id" in effective_filters:
            effective_filters["agent_id"] = _validate_and_trim_entity_id(
                effective_filters["agent_id"], "agent_id"
            )
        if "run_id" in effective_filters:
            effective_filters["run_id"] = _validate_and_trim_entity_id(
                effective_filters["run_id"], "run_id"
            )

        # Validate filters contains at least one entity ID
        if not any(key in effective_filters for key in ("user_id", "agent_id", "run_id")):
            raise ValueError(
                "filters must contain at least one of: user_id, agent_id, run_id. "
                "Example: filters={'user_id': 'u1'}"
            )

        limit = top_k
        scale_threshold_notice = detect_scale_threshold_from_top_k(top_k)

        # Apply enhanced metadata filtering if advanced operators are detected
        if self._has_advanced_operators(effective_filters):
            processed_filters = self._process_metadata_filters(effective_filters)
            # Remove logical/operator keys that have been reprocessed
            for logical_key in ("AND", "OR", "NOT"):
                effective_filters.pop(logical_key, None)
            for fk in list(effective_filters.keys()):
                if fk not in ("AND", "OR", "NOT", "user_id", "agent_id", "run_id") and isinstance(
                    effective_filters.get(fk), dict
                ):
                    effective_filters.pop(fk, None)
            effective_filters.update(processed_filters)

        keys, encoded_ids = process_telemetry_filters(effective_filters)
        capture_event(
            "mem0.search",
            self,
            {
                "limit": limit,
                "version": self.api_version,
                "keys": keys,
                "encoded_ids": encoded_ids,
                "sync_type": "async",
                "threshold": threshold,
                "explain": explain,
                "advanced_filters": bool(filters and self._has_advanced_operators(filters)),
            },
        )

        search_start = time.perf_counter()
        original_memories = await asyncio.to_thread(
            self._search_vector_store,
            query,
            effective_filters,
            limit,
            threshold,
            explain=explain,
            show_expired=show_expired,
        )
        search_elapsed_seconds = time.perf_counter() - search_start

        # Apply reranking if enabled and reranker is available
        if rerank and self.reranker and original_memories:
            try:
                # Run reranking in thread pool to avoid blocking async loop
                reranked_memories = await asyncio.to_thread(
                    self.reranker.rerank, query, original_memories, limit
                )
                original_memories = reranked_memories
            except Exception:
                logger.warning("Reranking failed, using original results", exc_info=True)

        if temporal_usage_notice:
            await display_temporal_usage_notice_async(
                self, "async", "search", *temporal_usage_notice
            )
        elif scale_threshold_notice:
            await display_scale_threshold_notice_async(
                self, "async", "search", *scale_threshold_notice
            )
        elif search_elapsed_seconds > PERFORMANCE_SLOW_QUERY_THRESHOLD_SECONDS:
            await display_performance_slow_query_notice_async(
                self,
                "async",
                "search",
                search_elapsed_seconds,
                top_k,
                len(original_memories),
            )
        else:
            await display_first_run_notice_async(self, "async", "search")
        return {"results": original_memories}

    async def update(
        self,
        memory_id,
        text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expiration_date: Any = _UNSET,
        data: Optional[str] = None,
    ):
        """
        Update a memory by ID asynchronously.

        Args:
            memory_id (str): ID of the memory to update.
            text (str, optional): New content to update the memory with.
            metadata (dict, optional): Metadata to update with the memory. Defaults to None.
                ``user_id``/``agent_id``/``run_id``/``actor_id`` are ignored here - they are
                immutable after creation.
            expiration_date (Any, optional): Date in YYYY-MM-DD format, or None to clear it.
            data (str, optional): Deprecated alias for ``text``. Will be removed in the next
                major release; use ``text`` instead.

        Returns:
            dict: Success message indicating the memory was updated.

        Example:
            >>> await m.update(memory_id="mem_123", text="Likes to play tennis on weekends")
            {'message': 'Memory updated successfully!'}
        """
        capture_event("mem0.update", self, {"memory_id": memory_id, "sync_type": "async"})

        if data is not None:
            logger.warning(
                "The `data` argument to update() is deprecated and will be removed in the "
                "next major release. Use `text` instead."
            )
            if text is None:
                text = data

        if text is None and metadata is None and expiration_date is _UNSET:
            raise ValueError("At least one of text, metadata, or expiration_date must be provided.")

        update_metadata = deepcopy(metadata) if metadata is not None else None
        if expiration_date is not _UNSET:
            update_metadata = update_metadata or {}
            update_metadata["expiration_date"] = _normalize_expiration_date(expiration_date)

        existing_embeddings = {}
        if text is not None:
            embeddings = await asyncio.to_thread(self.embedding_model.embed, text, "update")
            existing_embeddings[text] = embeddings

        await asyncio.to_thread(
            self._update_memory, memory_id, text, existing_embeddings, update_metadata
        )
        await display_first_run_notice_async(self, "async", "update")
        return {"message": "Memory updated successfully!"}

    async def delete(self, memory_id):
        """
        Delete a memory by ID asynchronously.

        Args:
            memory_id (str): ID of the memory to delete.
        """
        capture_event("mem0.delete", self, {"memory_id": memory_id, "sync_type": "async"})

        existing_memory = await asyncio.to_thread(self.vector_store.get, vector_id=memory_id)
        if existing_memory is None:
            raise ValueError(f"Memory with id {memory_id} not found")

        await asyncio.to_thread(self._delete_memory, memory_id, existing_memory)
        decay_usage_notice = detect_decay_usage_from_delete()
        if decay_usage_notice:
            await display_decay_usage_notice_async(self, "async", "delete", *decay_usage_notice)
        else:
            await display_first_run_notice_async(self, "async", "delete")
        return {"message": "Memory deleted successfully!"}

    async def delete_all(self, user_id=None, agent_id=None, run_id=None):
        """
        Delete all memories asynchronously.

        Args:
            user_id (str, optional): ID of the user to delete memories for. Defaults to None.
            agent_id (str, optional): ID of the agent to delete memories for. Defaults to None.
            run_id (str, optional): ID of the run to delete memories for. Defaults to None.
        """
        user_id = _validate_and_trim_entity_id(user_id, "user_id")
        agent_id = _validate_and_trim_entity_id(agent_id, "agent_id")
        run_id = _validate_and_trim_entity_id(run_id, "run_id")

        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id

        if not filters:
            raise ValueError(
                "At least one filter is required to delete all memories. If you want to delete all memories, use the `reset()` method."
            )

        keys, encoded_ids = process_telemetry_filters(filters)
        capture_event(
            "mem0.delete_all",
            self,
            {"keys": keys, "encoded_ids": encoded_ids, "sync_type": "async"},
        )
        deleted_count = await asyncio.to_thread(self._delete_scope, filters)

        decay_usage_notice = detect_decay_usage_from_delete_all(deleted_count)
        if decay_usage_notice:
            await display_decay_usage_notice_async(self, "async", "delete_all", *decay_usage_notice)
        else:
            await display_first_run_notice_async(self, "async", "delete_all")
        return {"message": "Memories deleted successfully!"}

    async def history(self, memory_id):
        """
        Get the history of changes for a memory by ID asynchronously.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory.
        """
        capture_event("mem0.history", self, {"memory_id": memory_id, "sync_type": "async"})
        history = await asyncio.to_thread(self.db.get_history, memory_id)
        await display_first_run_notice_async(self, "async", "history")
        return history

    async def _create_procedural_memory(self, messages, metadata=None, llm=None, prompt=None):
        """Create a procedural memory asynchronously.

        Args:
            messages (list): List of messages to create a procedural memory from.
            metadata (dict): Metadata to create a procedural memory from.
            llm (llm, optional): LLM to use for the procedural memory creation. Defaults to None.
            prompt (str, optional): Prompt to use for the procedural memory creation. Defaults to None.
        """
        logger.info("Creating procedural memory")

        parsed_messages = [
            {"role": "system", "content": prompt or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
            *messages,
            {"role": "user", "content": "Create procedural memory of the above conversation."},
        ]

        try:
            if llm is not None:
                # langchain-core is only needed to adapt messages for a custom
                # LangChain LLM. The default path uses self.llm and must not
                # require the optional dependency, mirroring the sync version.
                try:
                    from langchain_core.messages.utils import (
                        convert_to_messages,  # type: ignore
                    )
                except ImportError as e:
                    raise ImportError(
                        "langchain-core is required to pass a custom LLM to procedural memory. "
                        "Install it with 'pip install langchain-core'."
                    ) from e

                parsed_messages = convert_to_messages(parsed_messages)
                response = await asyncio.to_thread(llm.invoke, input=parsed_messages)
                procedural_memory = remove_code_blocks(response.content)
            else:
                procedural_memory = await asyncio.to_thread(
                    self.llm.generate_response, messages=parsed_messages
                )
                procedural_memory = remove_code_blocks(procedural_memory)

        except Exception as e:
            logger.error(f"Error generating procedural memory summary: {e}")
            raise

        if not procedural_memory:
            raise ValueError(
                "The LLM returned no content for the procedural memory summary. "
                "The model may have declined the request or returned an empty response."
            )

        if metadata is None:
            raise ValueError("Metadata cannot be done for procedural memory.")

        metadata = {**metadata, "memory_type": MemoryType.PROCEDURAL.value}
        embeddings = await asyncio.to_thread(
            self.embedding_model.embed, procedural_memory, memory_action="add"
        )
        memory_id = await asyncio.to_thread(
            self._create_memory, procedural_memory, {procedural_memory: embeddings}, metadata
        )
        capture_event(
            "mem0._create_procedural_memory", self, {"memory_id": memory_id, "sync_type": "async"}
        )

        result = {"results": [{"id": memory_id, "memory": procedural_memory, "event": "ADD"}]}

        return result

    async def reset(self):
        """Reset the memory store asynchronously.

        It does so by:
            Deleting and recreating the vector and entity collections
            Resetting the history database
        """
        logger.warning("Resetting all memories")
        await asyncio.to_thread(self._reset_stores)
        capture_event("mem0.reset", self, {"sync_type": "async"})
        await display_first_run_notice_async(self, "async", "reset")

    async def __aenter__(self):
        """Return this client for use in an async with-block; close() runs on exit."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close this client's own connections and let any exception propagate."""
        self.close()
        return False

    async def chat(self, query):
        """Refuse: chat is not part of the OSS memory client."""
        raise NotImplementedError("Chat function not implemented yet.")
