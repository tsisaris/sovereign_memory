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
from dataclasses import dataclass, field
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
        self.config = config
        self.terminal_error = terminal_error
        self.scope_of = scope_of
        self.may_modify = may_modify
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
        actor = event_envelope.get("actor_agent_id", event_envelope.get("agent_id"))
        operation = REMOVAL_OPERATIONS.get(event_envelope.get("event_type"))
        if operation is not None:
            return self._prepare_removal(
                event_id, event_envelope, verified_payload_bytes, operation
            )
        scope_id = self.scope_of(actor)

        messages, infer = self._messages_of(event_envelope, verified_payload_bytes)
        provenance = {
            key: event_envelope[key] for key in PROVENANCE_FIELDS if event_envelope.get(key)
        }
        provenance["event_id"] = event_id
        provenance["actor_agent_id"] = actor
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
        entity_embeddings = await self._embed_entities(
            memory,
            [a["text"] for a in decisions.get("memory", []) if a["event"] in ("ADD", "UPDATE")],
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
            key: await asyncio.to_thread(memory.embedding_model.embed, *key)
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
        """Embed the question outside the database worker."""
        memory = await self._read(lambda m: m)
        vector = await asyncio.to_thread(memory.embedding_model.embed, query, "search")
        return {"query": query, "vector": vector, "limit": limit}

    def retrieve(
        self, native_handle: duckdb.DuckDBPyConnection, scope: Any, prepared_query: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Search the memories the scope's visibility permits on the owner's handle; expired ones are left out.

        The search is limited to the producers the visibility names ("own": the
        principal; "granted": the granted actors; "pool": no producer limit).
        Every hit names the captured event it came from and is re-checked against
        the visibility; an unknown mode or a hit it does not permit raises
        instead of being returned.
        """
        visibility = scope.visibility
        if visibility.mode == "own":
            filters: Dict[str, Any] = {"actor_agent_id": scope.agent_id}
        elif visibility.mode == "granted":
            filters = {"actor_agent_id": {"in": sorted(visibility.actor_agent_ids)}}
        elif visibility.mode == "pool":
            filters = {}
        else:
            raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")
        memory = self._memory_for(native_handle)
        found = memory.vector_store.search(
            query=prepared_query["query"],
            vectors=prepared_query["vector"],
            top_k=prepared_query["limit"],
            filters=filters,
        )
        hits = []
        for item in found:
            payload = item.payload or {}
            if not visibility.permits(payload.get("actor_agent_id"), scope.agent_id):
                raise ValueError("Mem0 returned a memory outside the requested visibility")
            if _payload_is_expired(payload):
                continue
            hits.append(
                {
                    "id": item.id,
                    "event_id": payload.get("event_id"),
                    "actor_agent_id": payload.get("actor_agent_id"),
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
