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
import json
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Callable, Dict, List, Optional

import duckdb

from mem0.configs.base import MemoryConfig
from mem0.memory.main import (
    Memory,
    _build_filters_and_metadata,
    _build_session_scope,
    _payload_is_expired,
)

TIER_NAME = "personalization"
PROJECTION_VERSION = "mem0-history-vector-1"
EXPIRE_EVENT_TYPE = "system.expire"
PROVENANCE_FIELDS = (
    "event_id",
    "source_revision",
    "work_id",
    "source_artifact_id",
    "actor_agent_id",
    "exactness",
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
    ) -> None:
        """Keep the configuration; nothing is opened until the first owner call.

        terminal_error is the exception type that tells the owner a failure is
        permanent; pass the owner's TerminalProcessingError.
        """
        if config.vector_store.provider != "duckdb":
            raise ValueError("Mem0Tier needs the duckdb vector store provider")
        self.config = config
        self.terminal_error = terminal_error
        self._reader: Optional[Callable[..., Any]] = None
        self._memory: Optional[Memory] = None
        self._handle: Optional[duckdb.DuckDBPyConnection] = None

    def attach_reader(self, reader: Callable[..., Any]) -> None:
        """Receive the owner's reader; every read before a decision goes through it."""
        self._reader = reader

    def _memory_for(self, handle: duckdb.DuckDBPyConnection) -> Memory:
        """Return the Memory bound to the owner's handle, creating it on first use.

        Runs on the owner's worker. Raises RuntimeError if the owner ever
        passes a different handle than the one this tier was bound to.
        """
        if self._memory is None:
            self._memory = Memory(self.config, connection=handle)
            self._handle = handle
        elif handle is not self._handle:
            raise RuntimeError("Mem0Tier was bound to a different owner handle")
        return self._memory

    async def _read(self, func: Callable[[Memory], Any]) -> Any:
        """Run func(memory) on the owner's worker through the attached reader."""
        if self._reader is None:
            raise RuntimeError("Mem0Tier has no owner reader attached; register it with the owner")
        return await self._reader(lambda handle: func(self._memory_for(handle)))

    # ---- write seam -------------------------------------------------------------

    async def prepare(
        self, event_envelope: Dict[str, Any], verified_payload_bytes: Optional[bytes]
    ) -> PreparedWork:
        """Do all model work for one event, outside any write transaction.

        Raises the terminal error for payloads this tier cannot read, and any
        model error unchanged (the owner records it as a retryable failure).
        """
        if not self.config.enable_update_delete_branches:
            raise self.terminal_error(
                "Mem0Tier requires enable_update_delete_branches (the action flow)"
            )
        memory = await self._read(lambda m: m)
        event_id = event_envelope["event_id"]
        scope_id = event_envelope.get("actor_agent_id", event_envelope.get("agent_id"))
        if event_envelope.get("event_type") == EXPIRE_EVENT_TYPE:
            return self._prepare_expiry(event_id, scope_id, event_envelope, verified_payload_bytes)

        messages, infer = self._messages_of(event_envelope, verified_payload_bytes)
        provenance = {
            key: event_envelope[key] for key in PROVENANCE_FIELDS if event_envelope.get(key)
        }
        provenance["event_id"] = event_id
        provenance["actor_agent_id"] = scope_id
        metadata, filters = _build_filters_and_metadata(user_id=scope_id, input_metadata=provenance)
        if not infer:
            texts = [m["content"] for m in messages if m.get("role") != "system"]
            raw = [await asyncio.to_thread(memory.embedding_model.embed, t, "add") for t in texts]
            return PreparedWork(
                "raw", scope_id, event_id, messages, metadata, filters, {}, {}, {}, raw
            )

        facts = await asyncio.to_thread(memory._extract_facts, messages, metadata, filters)
        embeddings = await asyncio.to_thread(memory._embed_facts, facts)
        existing = await self._read(lambda m: m._find_existing_memories(facts, embeddings, filters))
        decisions, mapping = await asyncio.to_thread(memory._decide_memory_actions, existing, facts)
        for action in decisions.get("memory", []):
            text = action["text"]
            if action["event"] in ("ADD", "UPDATE") and text not in embeddings:
                purpose = "add" if action["event"] == "ADD" else "update"
                embeddings[text] = await asyncio.to_thread(
                    memory.embedding_model.embed, text, purpose
                )
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
        )

    def _prepare_expiry(
        self, event_id: str, _owner_scope: str, envelope: Dict[str, Any], payload: Optional[bytes]
    ) -> PreparedWork:
        """Read an owner-authored expiry event and name the captured event to remove."""
        if payload is None:
            raise self.terminal_error("Expiry event has no payload bytes")
        body = json.loads(payload.decode("utf-8"))
        target = body.get("target_event_id")
        owner_of_target = body.get("target_actor_agent_id")
        if (
            body.get("operation") != "expire"
            or target != envelope.get("causation_event_id")
            or not isinstance(owner_of_target, str)
            or not owner_of_target
        ):
            raise self.terminal_error("Expiry payload does not match its causation event")
        # The memories to remove belong to the expired event's actor, not to the owner.
        return PreparedWork("expire", owner_of_target, event_id, [], {}, {}, {}, {}, {}, [], target)

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
        if work.kind == "expire":
            removed = self._expire(memory, work)
            status = "processed" if removed else "processed_no_new_fact"
        elif work.kind == "raw":
            texts = [m for m in work.messages if m.get("role") != "system"]
            for message, embedding in zip(texts, work.raw_embeddings, strict=True):
                meta = dict(work.metadata, role=message["role"])
                memory._create_memory(message["content"], {message["content"]: embedding}, meta)
            memory.db.save_messages(work.messages, _scope(work))
            status = "processed" if texts else "processed_no_new_fact"
        else:
            returned = memory._execute_memory_actions(
                work.decisions, work.mapping, work.embeddings, work.metadata, work.filters
            )
            memory.db.save_messages(work.messages, _scope(work))
            changed = [r for r in returned if r.get("event") in ("ADD", "UPDATE", "DELETE")]
            status = "processed" if changed else "processed_no_new_fact"
        return TierResult(status, "mem0-duckdb", version("mem0_duckdb"), PROJECTION_VERSION)

    def _expire(self, memory: Memory, work: PreparedWork) -> int:
        """Delete every memory derived from the expired event; return how many were removed."""
        store = memory.vector_store
        count = store.col_info()["count"]
        if not count:
            return 0
        rows = store.list(
            filters={"user_id": work.scope_id, "event_id": work.target_event_id}, top_k=count
        )
        for row in rows:
            memory._delete_memory(row.id, existing_memory=row)
        return len(rows)

    # ---- read seam --------------------------------------------------------------

    async def prepare_query(self, scope: Any, query: str, limit: int) -> Dict[str, Any]:
        """Embed the question outside the database worker."""
        memory = await self._read(lambda m: m)
        vector = await asyncio.to_thread(memory.embedding_model.embed, query, "search")
        return {"query": query, "vector": vector, "limit": limit}

    def retrieve(
        self, native_handle: duckdb.DuckDBPyConnection, scope: Any, prepared_query: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Search the scope's own memories on the owner's handle; expired ones are left out.

        Every hit names the captured event it came from. A memory without that
        provenance, or from another identity, raises instead of being returned.
        """
        memory = self._memory_for(native_handle)
        found = memory.vector_store.search(
            query=prepared_query["query"],
            vectors=prepared_query["vector"],
            top_k=prepared_query["limit"],
            filters={"user_id": scope.agent_id},
        )
        hits = []
        for item in found:
            payload = item.payload or {}
            if payload.get("user_id") != scope.agent_id:
                raise ValueError("Mem0 returned a memory outside the requested identity scope")
            if _payload_is_expired(payload):
                continue
            hits.append(
                {
                    "id": item.id,
                    "event_id": payload.get("event_id"),
                    "memory": payload.get("data"),
                    "score": item.score,
                    "source_artifact_id": payload.get("source_artifact_id"),
                    "source_revision": payload.get("source_revision"),
                }
            )
        return hits


def _scope(work: PreparedWork) -> str:
    """Return the session scope string Mem0 records messages under for this work."""
    return _build_session_scope(work.filters)
