"""Mem0 as the "personalization" tier of the Sovereign Memory owner service.

The owner calls this tier through two seams that are matched here structurally
(this package does not import the owner package):

* write: ``prepare(event, verified_bytes)`` does every model call outside any
  write transaction; ``apply(handle, prepared)`` then writes the memories, their
  history and nothing else on the owner's borrowed native handle, inside the
  transaction that also records the receipt;
* read: ``prepare_query`` embeds the question outside the database worker;
  ``retrieve(handle, scope, prepared)`` searches on the owner's handle.

The tier never opens, commits, rolls back or closes a connection. Its reads
before a decision go through the reader the owner hands to ``attach_reader``.
A captured event is a Mem0 capture only if its payload says so; anything else is
refused with a terminal failure rather than reported as processed.
"""

import asyncio
import copy
import json
import math
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any, Callable, Dict, List, Optional

import duckdb

from mem0.configs.base import MemoryConfig
from mem0.embeddings.received_numbers import NumbersMissing
from mem0.memory.main import (
    Memory,
    _build_filters_and_metadata,
    _build_session_scope,
    _payload_is_expired,
)
from mem0.utils.factory import LlmFactory

TIER_NAME = "personalization"
PROJECTION_VERSION = "mem0-history-vector-1"
# Owner-authored removal events and the operation each one carries in its payload.
REMOVAL_OPERATIONS = {"system.expire": "expire", "system.forget": "forget"}
PROVENANCE_FIELDS = (
    "event_id",
    "source_revision",
    "work_id",
    "source_artifact_id",
    "actor_agent_id",
    "exactness",
)


class EmbeddingIndexMismatch(RuntimeError):
    """Mem0's own vector table holds memories from a different model than the configured one.

    Mem0 keeps its vectors in one table of one fixed width, so this guard stays
    for that table only; the owner's per-model numbers tables have no such rule.

    Mixing them in one search would compare vectors from different models (even at
    the same dimension), so the tier refuses to write or search until the index is
    rebuilt from the original events. The original memories are never altered.
    """


def embedding_identity(config: MemoryConfig) -> str:
    """Return the declared provider, model, revision and dimensions as unambiguous JSON.

    It is stamped on every stored memory and compared before any write or search.
    Raises ValueError for missing revision/model or invalid dimensions. A declaration
    is not proof of the served revision; a provider must verify its response separately.
    """
    settings = config.embedder.config
    model = (
        settings.get("model") if isinstance(settings, dict) else getattr(settings, "model", None)
    )
    if not isinstance(model, str) or not model:
        raise ValueError("The embedder configuration names no model")
    dims = getattr(config.vector_store.config, "embedding_model_dims", None)
    revision = config.embedder.revision
    if not isinstance(revision, str) or not revision:
        raise ValueError("The Owner embedder configuration names no model revision")
    if isinstance(dims, bool) or not isinstance(dims, int) or dims <= 0:
        raise ValueError("The Owner embedder needs positive integer dimensions")
    identity = {
        "format": "mem0-embedding-v2",
        "provider": config.embedder.provider,
        "model": model,
        "revision": revision,
        "dimensions": dims,
    }
    return json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    )


class TerminalCaptureError(Exception):
    """The event can never be processed by this tier (for example, an unsupported payload).

    Used when no other type is given. Pass the owner's TerminalProcessingError
    as ``terminal_error`` so the owner records the failure as terminal.
    """


@dataclass(frozen=True)
class TierResult:
    """What apply reports back: the owner's receipt fields, with the truthful status."""

    status: str
    processor_id: str
    processor_version: str
    projection_version: str
    error_ref: Optional[str] = None


@dataclass
class PreparedWork:
    """Everything apply needs, computed outside the transaction (no database handles inside)."""

    kind: str
    scope_id: str
    event_id: str
    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    filters: Dict[str, Any]
    embeddings: Dict[str, List[float]]
    decisions: Dict[str, Any]
    mapping: Dict[str, str]
    raw_embeddings: List[List[float]]
    target_event_id: Optional[str] = None
    target_actor: Optional[str] = None
    actor: Optional[str] = None
    entity_embeddings: Dict[Any, List[float]] = field(default_factory=dict)


class Mem0Tier:
    """The personalization tier: Mem0 fact extraction, decisions and storage on DuckDB.

    Build it with a MemoryConfig whose vector store provider is "duckdb"; its
    llm and embedder settings are used as given and no model is chosen here.
    Set ``enable_update_delete_branches`` in the config for the full
    ADD/UPDATE/DELETE/NONE flow; without it captured messages are stored through
    Mem0's ADD-only pipeline, which this tier does not support and refuses.
    """

    tier_name = TIER_NAME

    def __init__(
        self,
        config: MemoryConfig,
        terminal_error: type[Exception] = TerminalCaptureError,
        scope_of: Callable[[str], str] = lambda actor: actor,
        may_modify: Callable[[str, Optional[str]], bool] = lambda actor, author: actor == author,
    ) -> None:
        """Keep the configuration; nothing is opened until the first owner call.

        terminal_error is the exception type that tells the owner a failure is
        permanent; pass the owner's TerminalProcessingError.

        scope_of maps an event's authenticated actor to the Mem0 identity scope
        (user_id) its memories are stored and consolidated under. The default
        keeps each actor's memories separate (the restricted profile); a shared
        pool deployment passes a function returning its one pool id. The actor
        is always kept as provenance in each memory's payload, and who may read
        a memory is decided by the visibility the owner passes to retrieve, never
        by this mapping.

        may_modify(actor, author) says whether an event's actor may update or
        delete a memory authored by another actor. The default refuses anything
        but one's own memory (fail closed); a shared-pool deployment passes the
        rule its permission owner defines. A refused change is a terminal failure.
        """
        if config.vector_store.provider != "duckdb":
            raise ValueError("Mem0Tier needs the duckdb vector store provider")
        if config.embedder.provider != "received_numbers":
            raise ValueError(
                "Mem0Tier needs the received_numbers embedder: memory never makes numbers, "
                "so no model-calling embedder is accepted"
            )
        self.config = config
        self.embedding_identity = embedding_identity(config)
        self.model_id: str = config.embedder.config["model"]
        self.revision: str = config.embedder.revision
        self.label = f"{self.model_id}@{self.revision}"
        self._services: Any = None
        self.terminal_error = terminal_error
        self.scope_of = scope_of
        self.may_modify = may_modify
        self._reader: Optional[Callable[..., Any]] = None
        self._memory: Optional[Memory] = None
        self._handle: Optional[duckdb.DuckDBPyConnection] = None

    def attach_reader(self, reader: Callable[..., Any]) -> None:
        """Receive the owner's reader; every read before a decision goes through it."""
        self._reader = reader

    async def memory_with_language_model(self, llm_section: Dict[str, Any]) -> Memory:
        """Return the tier's Memory with its language model replaced by one built from llm_section.

        It takes an llm section ({"provider", "config"}), reads the tier's Memory through the
        owner's reader and returns a shallow copy that shares the storage and replaces only
        the model client, so a caller can run Mem0's own model steps with another model. It
        never stores anything and never changes the tier's own Memory.
        """
        memory = await self._read(lambda m: m)
        replaced = copy.copy(memory)
        replaced.llm = LlmFactory.create(llm_section["provider"], llm_section["config"])
        return replaced

    def attach_numbers(self, services: Any) -> None:
        """Receive the owner's numbers services: lookup of received numbers, owed requests, question door."""
        self._services = services

    def model_labels(self) -> List[str]:
        """Return the identity of the one model whose numbers Mem0's own vector table holds."""
        return [self.embedding_identity]

    def _need_services(self) -> Any:
        """Return the numbers services or raise RuntimeError if the owner never attached them."""
        if self._services is None:
            raise RuntimeError(
                "Mem0Tier has no numbers services attached; register it with the owner"
            )
        return self._services

    def _bind_embedder(self, memory: Memory) -> None:
        """Give Mem0's received-numbers embedder the owner's lookup; it makes no numbers itself."""
        services = self._need_services()
        embedder = memory.embedding_model
        embedder.attach_lookup(services.lookup)

    async def _owe(self, event_id: str, missing: NumbersMissing) -> None:
        """Make the open requests for this event exactly the texts whose numbers are owed now.

        A request for a text an earlier attempt produced and this attempt did not
        is closed, so no request stays open for text that no longer exists.
        """
        services = self._need_services()
        await services.request_derived(
            self.model_id, self.revision, event_id, missing.texts, str(missing)
        )

    def _retire_replaced(self, memory: Memory, work: "PreparedWork", handle: Any) -> None:
        """Retire the derived pieces of memory texts an UPDATE or DELETE in this work replaces.

        Runs inside apply on the owner's transaction, before the change, so the old
        text (read from the memory about to change) stops counting as missing for any
        model. Nothing is deleted.
        """
        services = self._need_services()
        for action in work.decisions.get("memory", []):
            if action["event"] not in ("UPDATE", "DELETE"):
                continue
            target = memory.vector_store.get(vector_id=work.mapping[action["id"]])
            payload = (target.payload if target is not None else None) or {}
            if payload.get("event_id") and payload.get("data"):
                services.retire_derived(handle, payload["event_id"], payload["data"])

    def initialize_storage(self, native_tx_handle: duckdb.DuckDBPyConnection) -> None:
        """Bind Memory to the owner's handle and create its tables, in the owner's transaction.

        The owner calls this once at start, on its worker, inside a short
        transaction it commits or rolls back. It creates the vector, entity and
        history tables and no model client (those are made on first use, off the
        worker). Raises RuntimeError if the tier was already bound to another handle.
        """
        if self._memory is None:
            self._memory = Memory(self.config, connection=native_tx_handle)
            self._handle = native_tx_handle
            self._memory.entity_store
        elif native_tx_handle is not self._handle:
            raise RuntimeError("Mem0Tier was bound to a different owner handle")

    def _memory_for(self, handle: duckdb.DuckDBPyConnection) -> Memory:
        """Return the Memory bound to the owner's handle; it never creates one.

        Storage is created only by initialize_storage, so a read-only callback never
        runs DDL. Raises RuntimeError if the owner never initialized the tier or
        passes a different handle than the one it was bound to.
        """
        if self._memory is None:
            raise RuntimeError("Mem0Tier storage is not initialized; the owner must start it first")
        if handle is not self._handle:
            raise RuntimeError("Mem0Tier was bound to a different owner handle")
        return self._memory

    def reset_projection(self, handle: duckdb.DuckDBPyConnection, max_items: int) -> None:
        """Reset bounded, source-backed memory/entity vectors on the caller's transaction.

        Refuse unmanaged memories or entity links rather than discard records without
        recoverable Owner sources. History and source messages are never reset.
        """
        memory = self._memory_for(handle)
        store, entities = memory.vector_store, memory.entity_store
        for table in (store.collection_name, entities.collection_name):
            if handle.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > max_items:
                raise ValueError("Projection exceeds the requested rebuild item limit")
        rows = handle.execute(f"SELECT id, payload FROM {store.collection_name}").fetchall()
        identifiers = {row[0] for row in rows}
        for _, payload in rows:
            data = json.loads(payload or "{}")
            source = handle.execute(
                "SELECT e.actor_agent_id FROM memory_events e JOIN memory_receipts r USING (event_id) "
                "WHERE e.event_id = ? AND r.tier = ? AND (e.schema_version LIKE '2.%' "
                "OR json_extract_string(e.raw_event, '$.tier') = r.tier)",
                [data.get("event_id"), TIER_NAME],
            ).fetchone()
            if source is None or source[0] != data.get("actor_agent_id"):
                raise ValueError("Mem0 projection contains a memory without Owner provenance")
        for (payload,) in handle.execute(
            f"SELECT payload FROM {entities.collection_name}"
        ).fetchall():
            links = json.loads(payload or "{}").get("linked_memory_ids")
            if not isinstance(links, list) or not links or not set(links).issubset(identifiers):
                raise ValueError("Mem0 entity projection contains unmanaged or orphaned links")
        entities.reset()
        store.reset()

    def _require_matching_index(self, memory: Memory) -> None:
        """Raise EmbeddingIndexMismatch if any stored memory has another embedding identity.

        Memories stored before identities were recorded count as unknown, so they
        also need a rebuild. Reads only, on the owner's handle.
        """
        self._require_embedding_identity(memory)
        table = memory.vector_store.collection_name
        (other,) = memory.vector_store.connection.execute(
            f"SELECT count(*) FROM {table} "
            "WHERE json_extract_string(payload, '$.embedding_identity') IS DISTINCT FROM ?",
            [self.embedding_identity],
        ).fetchone()
        if other:
            raise EmbeddingIndexMismatch(
                f"{other} stored memories were embedded by a model other than "
                f"{self.embedding_identity!r}; rebuild the index from the source events"
            )

    def _require_embedding_identity(self, memory: Memory) -> None:
        """Refuse changed configuration or a conflicting exposed revision; never create clients."""
        if embedding_identity(self.config) != self.embedding_identity:
            raise EmbeddingIndexMismatch("The embedding configuration changed after tier creation")
        engine = memory._embedding_model
        revision = getattr(engine, "revision", None)
        if revision is not None and revision != self.config.embedder.revision:
            raise EmbeddingIndexMismatch("The served embedding revision differs from configuration")

    async def _read(self, func: Callable[[Memory], Any]) -> Any:
        """Run func(memory) on the owner's worker through the attached reader."""
        if self._reader is None:
            raise RuntimeError("Mem0Tier has no owner reader attached; register it with the owner")
        return await self._reader(lambda handle: func(self._memory_for(handle)))

    def _embed_text(self, memory: Memory, text: str, purpose: str) -> List[float]:
        """Resolve the lazy client and embed text inside the calling model worker thread.

        Call through asyncio.to_thread so SDK construction never runs on the event
        loop or the owner's database worker. Provider errors propagate unchanged.
        """
        return self._validate_vector(memory.embedding_model.embed(text, purpose))

    def _validate_vector(self, vector: Any) -> List[float]:
        """Refuse wrong-width or nonfinite provider vectors before projection or search."""
        dimensions = self.config.vector_store.config.embedding_model_dims
        if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
            raise ValueError("Mem0 embedding provider returned an unexpected vector width")
        try:
            valid = all(type(v) in (int, float) and math.isfinite(v) for v in vector)
        except OverflowError as error:
            raise ValueError("Mem0 embedding provider returned an oversized value") from error
        if not valid:
            raise ValueError("Mem0 embedding provider returned a nonfinite or nonnumeric vector")
        return [float(value) for value in vector]

    # ---- write seam -------------------------------------------------------------

    async def prepare(
        self, event_envelope: Dict[str, Any], verified_payload_bytes: Optional[bytes]
    ) -> PreparedWork:
        """Prepare one event from received numbers, outside any write transaction.

        Raises the terminal error for payloads this tier cannot read. When a
        text has no received numbers it records the owed numbers and raises
        NumbersMissing ("numbers not yet supplied"), which the owner records as a
        retryable failure; no vector is ever invented. Language-model work
        (fact extraction) runs in worker threads, never on the owner's database
        worker and never inside a write transaction.
        """
        event_id = event_envelope["event_id"]
        try:
            prepared = await self._prepare(event_envelope, verified_payload_bytes)
        except NumbersMissing as missing:
            await self._owe(event_id, missing)
            raise
        if prepared.kind != "remove":
            await self._need_services().derived_complete(self.model_id, self.revision, event_id)
        return prepared

    async def _prepare(
        self, event_envelope: Dict[str, Any], verified_payload_bytes: Optional[bytes]
    ) -> PreparedWork:
        """Do the work of prepare; NumbersMissing from the embedder propagates to prepare."""
        event_id = event_envelope["event_id"]
        operation = REMOVAL_OPERATIONS.get(event_envelope.get("event_type"))
        if operation is not None:
            # A removal is native storage work only: no model client and no memory read.
            return self._prepare_removal(
                event_id, event_envelope, verified_payload_bytes, operation
            )
        if not self.config.enable_update_delete_branches:
            raise self.terminal_error(
                "Mem0Tier requires enable_update_delete_branches (the action flow)"
            )
        memory = await self._read(lambda m: m)
        self._bind_embedder(memory)
        await self._read(self._require_matching_index)
        actor = event_envelope.get("actor_agent_id", event_envelope.get("agent_id"))
        scope_id = self.scope_of(actor)

        messages, infer = self._messages_of(event_envelope, verified_payload_bytes)
        provenance = {
            key: event_envelope[key] for key in PROVENANCE_FIELDS if event_envelope.get(key)
        }
        provenance["embedding_identity"] = self.embedding_identity
        provenance["event_id"] = event_id
        provenance["actor_agent_id"] = actor
        metadata, filters = _build_filters_and_metadata(user_id=scope_id, input_metadata=provenance)
        if not infer:
            texts = [m["content"] for m in messages if m.get("role") != "system"]
            raw = [await asyncio.to_thread(self._embed_text, memory, t, "add") for t in texts]
            self._require_embedding_identity(memory)
            return PreparedWork(
                "raw", scope_id, event_id, messages, metadata, filters, {}, {}, {}, raw
            )

        facts = await asyncio.to_thread(memory._extract_facts, messages, metadata, filters)
        embeddings = {
            text: self._validate_vector(vector)
            for text, vector in (await asyncio.to_thread(memory._embed_facts, facts)).items()
        }
        self._require_embedding_identity(memory)
        existing = await self._read(lambda m: m._find_existing_memories(facts, embeddings, filters))
        decisions, mapping = await asyncio.to_thread(memory._decide_memory_actions, existing, facts)
        for action in decisions.get("memory", []):
            text = action["text"]
            if action["event"] in ("ADD", "UPDATE") and text not in embeddings:
                purpose = "add" if action["event"] == "ADD" else "update"
                embeddings[text] = await asyncio.to_thread(self._embed_text, memory, text, purpose)
        entity_embeddings = await self._embed_entities(
            memory,
            [a["text"] for a in decisions.get("memory", []) if a["event"] in ("ADD", "UPDATE")],
        )
        self._require_embedding_identity(memory)
        return PreparedWork(
            "actions",
            scope_id,
            event_id,
            messages,
            metadata,
            filters,
            embeddings,
            decisions,
            mapping,
            [],
            actor=actor,
            entity_embeddings=entity_embeddings,
        )

    async def _embed_entities(self, memory: Memory, new_texts: List[str]) -> Dict[Any, List[float]]:
        """Embed, outside the transaction, every entity text apply will link.

        Covers the entities of each added or updated memory text. Unlinking an
        updated or deleted memory needs no embedding: an entity's text, and so its
        vector, does not change. Embedding goes through the model, never the
        database worker.
        """
        needed = memory._entity_texts_to_embed(new_texts)
        return {
            key: await asyncio.to_thread(self._embed_text, memory, *key)
            for key in dict.fromkeys(needed)
        }

    def _prepare_removal(
        self, event_id: str, envelope: Dict[str, Any], payload: Optional[bytes], operation: str
    ) -> PreparedWork:
        """Validate an owner-authored expire or forget event and prepare its removal.

        The payload must be a JSON object whose operation is the one the event type
        names and whose target_event_id equals the envelope's causation_event_id;
        the target event id and its actor must be non-empty strings. Anything else
        is a terminal failure and nothing is prepared, so a malformed removal can
        never widen what is deleted. apply re-reads the memories derived from the
        target event by that actor on the owner's handle; removing them and
        unlinking their entities needs no model call.
        """
        if payload is None:
            raise self.terminal_error(f"The {operation} event has no payload bytes")
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise self.terminal_error(f"The {operation} payload is not JSON: {error}") from error
        target = body.get("target_event_id") if isinstance(body, dict) else None
        target_actor = body.get("target_actor_agent_id") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("operation") != operation
            or not isinstance(target, str)
            or not target
            or target != envelope.get("causation_event_id")
            or not isinstance(target_actor, str)
            or not target_actor
        ):
            raise self.terminal_error(f"The {operation} payload does not match its causation event")
        return PreparedWork(
            kind="remove",
            scope_id=target_actor,
            event_id=event_id,
            messages=[],
            metadata={},
            filters={},
            embeddings={},
            decisions={},
            mapping={},
            raw_embeddings=[],
            target_event_id=target,
            target_actor=target_actor,
            actor=envelope.get("actor_agent_id"),
        )

    @staticmethod
    def _derived(memory: Memory, target_event_id: Optional[str], actor: str) -> list:
        """List the stored memories derived from one captured event by one actor (read only)."""
        store = memory.vector_store
        count = store.col_info()["count"]
        if not count:
            return []
        return store.list(
            filters={"event_id": target_event_id, "actor_agent_id": actor}, top_k=count
        )

    def _messages_of(self, envelope: Dict[str, Any], payload: Optional[bytes]):
        """Return (messages, infer) from an event, or raise the terminal error.

        A legacy 1.0.0 event carries its text in ``content``. A version 2 event's
        verified payload must be a JSON object with a non-empty ``messages`` list
        of {"role", "content"} dicts and, optionally, a boolean ``infer``.
        """
        if envelope.get("schema_version") == "1.0.0":
            return [{"role": "user", "content": envelope["content"]}], True
        try:
            body = json.loads((payload or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise self.terminal_error(f"Payload is not a JSON capture: {error}") from error
        messages = body.get("messages") if isinstance(body, dict) else None
        infer = body.get("infer", True) if isinstance(body, dict) else True
        if (
            not isinstance(messages, list)
            or not messages
            or not all(
                isinstance(m, dict)
                and isinstance(m.get("role"), str)
                and isinstance(m.get("content"), str)
                for m in messages
            )
            or not isinstance(infer, bool)
        ):
            raise self.terminal_error("Payload is not a Mem0 capture: needs a messages list")
        return messages, infer

    def apply(
        self, native_tx_handle: duckdb.DuckDBPyConnection, prepared_work: PreparedWork
    ) -> TierResult:
        """Write the prepared memories on the owner's handle and say truthfully what happened.

        No model call, cursor, commit or close happens here. Every UPDATE and
        DELETE re-reads its target and re-checks identity scope first.
        """
        memory = self._memory_for(native_tx_handle)
        work = prepared_work
        if work.kind != "remove":
            self._require_matching_index(memory)
        with memory.use_planned_embeddings(work.entity_embeddings):
            if work.kind == "remove":
                removed = self._remove(memory, work)
                status = "processed" if removed else "processed_no_new_fact"
            elif work.kind == "raw":
                texts = [m for m in work.messages if m.get("role") != "system"]
                for message, embedding in zip(texts, work.raw_embeddings, strict=True):
                    meta = dict(work.metadata, role=message["role"])
                    memory._create_memory(message["content"], {message["content"]: embedding}, meta)
                memory.db.save_messages(work.messages, _scope(work))
                status = "processed" if texts else "processed_no_new_fact"
            else:
                self._require_may_modify(memory, work)
                self._retire_replaced(memory, work, native_tx_handle)
                returned = memory._execute_memory_actions(
                    work.decisions, work.mapping, work.embeddings, work.metadata, work.filters
                )
                memory.db.save_messages(work.messages, _scope(work))
                changed = [r for r in returned if r.get("event") in ("ADD", "UPDATE", "DELETE")]
                status = "processed" if changed else "processed_no_new_fact"
        if memory.planned_embedding_misses:
            # Entity upkeep needed text that was not embedded before the transaction: refuse
            # to report success, so the owner rolls back and the event is prepared again.
            raise RuntimeError(
                "Entity upkeep needed embeddings that were not prepared: "
                f"{memory.planned_embedding_misses}"
            )
        return TierResult(status, "mem0-duckdb", version("mem0_duckdb"), PROJECTION_VERSION)

    def _require_may_modify(self, memory: Memory, work: PreparedWork) -> None:
        """Refuse, as a terminal failure, any UPDATE or DELETE of a memory the actor may not change.

        A target that has disappeared since the plan raises ValueError instead,
        which the owner records as retryable.
        """
        for action in work.decisions.get("memory", []):
            if action["event"] not in ("UPDATE", "DELETE"):
                continue
            target = memory.vector_store.get(vector_id=work.mapping[action["id"]])
            if target is None:
                # Changed by someone else since the plan was made: prepare again.
                raise ValueError(f"Memory {work.mapping[action['id']]} no longer exists")
            author = (target.payload or {}).get("actor_agent_id")
            if not self.may_modify(work.actor or "", author):
                raise self.terminal_error(
                    f"Actor {work.actor!r} may not change a memory authored by {author!r}"
                )

    def _remove(self, memory: Memory, work: PreparedWork) -> int:
        """Delete every memory derived from the removed event; return how many were removed.

        Each deletion goes through Mem0's own delete, so history records it and the
        entity links are updated. Messages the source ledger holds are not touched.
        """
        rows = self._derived(memory, work.target_event_id, work.target_actor or "")
        for row in rows:
            memory._delete_memory(row.id, existing_memory=row)
        return len(rows)

    # ---- read seam --------------------------------------------------------------

    async def prepare_query(self, scope: Any, query: str, limit: int) -> Dict[str, Any]:
        """Fetch the question's numbers, once per model fresh for derived texts, from the supplier.

        A model that is stale for derived texts, or whose question numbers the
        supplier cannot give, is left out with its reason and is never reported as
        an empty ranking. Memory never embeds the question itself.
        """
        vectors, left_out = await self._need_services().question_vectors("derived", query)
        return {"vectors": vectors, "left_out": left_out, "limit": limit}

    def retrieve(
        self, native_handle: duckdb.DuckDBPyConnection, scope: Any, prepared_query: Dict[str, Any]
    ) -> Any:
        """Rank the memories the scope's visibility permits, once per model, from that model's own numbers.

        Each ranking joins one model's numbers table of derived pieces to the live
        memories (same event, same text fingerprint) and orders them by cosine
        distance (lower is nearer); Mem0's own vector table is not searched, so a
        further model joins without touching it. The search is limited to the
        producers the visibility names; expired memories are left out. Every hit
        names the captured event it came from and is re-checked against the
        visibility; an unknown mode or a hit it does not permit raises instead of
        being returned. It never writes and never makes a number.
        """
        visibility = scope.visibility
        condition: str
        params: List[Any]
        if visibility.mode == "own":
            condition, params = (
                "json_extract_string(v.payload, '$.actor_agent_id') = ?",
                [scope.agent_id],
            )
        elif visibility.mode == "granted":
            actors = sorted(visibility.actor_agent_ids)
            if not actors:
                return self._rankings({}, dict(prepared_query["left_out"]))
            condition = "json_extract_string(v.payload, '$.actor_agent_id') IN (SELECT unnest(?))"
            params = [actors]
        elif visibility.mode == "pool":
            condition, params = "TRUE", []
        else:
            raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")
        table = self._memory_for(native_handle).vector_store.collection_name
        rankings: Dict[str, List[Dict[str, Any]]] = {}
        for label, (model, vector) in prepared_query["vectors"].items():
            rows = native_handle.execute(
                f"SELECT v.id, v.payload, "
                f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
                f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
                f"JOIN {table} v ON json_extract_string(v.payload, '$.event_id') = p.event_id "
                "AND sha256(json_extract_string(v.payload, '$.data')) = p.text_fingerprint "
                "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
                "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
                "WHERE NOT n.stale AND p.source = 'derived' AND x.piece_id IS NULL "
                f"AND r.target_event_id IS NULL AND {condition} ORDER BY distance, v.id",
                [vector, *params],
            ).fetchall()
            hits = []
            for memory_id, raw, distance in rows:
                payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                if not visibility.permits(payload.get("actor_agent_id"), scope.agent_id):
                    raise ValueError("Mem0 returned a memory outside the requested visibility")
                if _payload_is_expired(payload):
                    continue
                hits.append(
                    {
                        "item_id": memory_id,
                        "id": memory_id,
                        "event_id": payload.get("event_id"),
                        "actor_agent_id": payload.get("actor_agent_id"),
                        "memory": payload.get("data"),
                        "distance": distance,
                        "source_artifact_id": payload.get("source_artifact_id"),
                        "source_revision": payload.get("source_revision"),
                    }
                )
            rankings[label] = hits[: prepared_query["limit"]]
        return self._rankings(rankings, dict(prepared_query["left_out"]))

    @staticmethod
    def _rankings(rankings: Dict[str, List[Dict[str, Any]]], left_out: Dict[str, str]) -> Any:
        """Return the rankings in the owner's TierRankings shape without importing the owner package."""
        return _TierRankings(rankings, left_out)


@dataclass(frozen=True)
class _TierRankings:
    """Per-model rankings and the models left out, matching the owner's TierRankings by shape."""

    rankings: Dict[str, List[Dict[str, Any]]]
    left_out: Dict[str, str]


def _scope(work: PreparedWork) -> str:
    """Return the session scope string Mem0 records messages under for this work."""
    return _build_session_scope(work.filters)
