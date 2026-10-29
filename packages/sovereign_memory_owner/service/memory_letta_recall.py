"""Letta recall messages as the owner's episodic tier, written on the owner's own handle.

Every captured event becomes Letta recall messages of the producing actor (the
Letta agent), stored through the maintained LettaMemoryStore on the owner's
borrowed native transaction handle, so the messages and the receipt commit
together. Message ids are derived from the event id and each message's otid is
the event id, so a replay never adds a second copy and removal finds exactly
the messages an event created. Retrieval is Letta's own recall search: when
the supplier's numbers exist it ranks the events by meaning, one ranking per
model from that model's own numbers, and returns each event's messages; when
they do not it is a case-insensitive substring match on message text, newest
first. No model is called by this tier and the question's numbers come from
the supplier; Letta's archival passages (embeddings) are a separate memory this
tier does not fill.
"""

import uuid
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import duckdb
from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.schemas.message import Message

from service.memory_event_ranking import question_numbers_for_events, rank_events
from service.memory_numbers_services import NumbersServices
from service.memory_processor_seam import ProcessorResult, RetrievalScope, TierRankings
from service.memory_tier_support import actor_of, event_text, is_removal, removal_target

TIER_NAME = "episodic"
PROCESSOR_ID = "letta-duckdb-recall"
PROJECTION_VERSION = "letta-recall-1"
MESSAGE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "sovereign-memory-owner/letta-message")
# Role given to an event whose payload is plain text or JSON rather than role messages.
DEFAULT_ROLE = "user"


def message_id(event_id: str, index: int) -> str:
    """Return the deterministic Letta message id of one message of a captured event; pure."""
    return "message-" + str(uuid.uuid5(MESSAGE_NAMESPACE, f"{event_id}:{index}"))


@dataclass(frozen=True)
class PreparedRecall:
    """Messages to store for an agent, or (when removal_target is set) the event to unstore.

    Holds plain data only, never a database handle.
    """

    agent_id: str
    messages: tuple[Message, ...] = ()
    removal_target: str | None = None


def _has_recall_schema(handle: duckdb.DuckDBPyConnection) -> bool:
    """Say whether Letta's reserved messages table exists yet; reads only."""
    found = handle.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = 'letta' AND table_name = 'messages'"
    ).fetchone()[0]
    return found == 1


class LettaRecallTier:
    """Owner processor and retriever for the episodic tier, backed by Letta recall.

    organization_id is the Letta authorization scope every row is written and
    read under; it comes from the deployment's configuration. The tier never
    opens, commits, rolls back or closes a connection.
    """

    tier_name = TIER_NAME

    def __init__(self, organization_id: str) -> None:
        """Remember the Letta organization; nothing is opened until the first owner call."""
        if not isinstance(organization_id, str) or not organization_id:
            raise ValueError("LettaRecallTier needs a non-empty organization_id")
        self.organization_id = organization_id
        self._store: LettaMemoryStore | None = None
        self._handle: duckdb.DuckDBPyConnection | None = None
        self._services: NumbersServices | None = None

    def attach_numbers(self, services: NumbersServices) -> None:
        """Receive the owner's numbers services, used only to get the numbers of a search question."""
        self._services = services

    def model_labels(self) -> list[str]:
        """Return the model labels a rebuild of this tier covers: none, as it holds no numbers."""
        return []

    def _store_for(self, handle: duckdb.DuckDBPyConnection) -> LettaMemoryStore:
        """Return the store bound to the owner's handle, creating it on first use.

        Raises RuntimeError if the owner ever passes a different handle.
        """
        if self._store is None:
            self._store = LettaMemoryStore(handle)
            self._handle = handle
        elif handle is not self._handle:
            raise RuntimeError("LettaRecallTier was bound to a different owner handle")
        return self._store

    # ---- write seam -------------------------------------------------------------

    async def prepare(
        self, event_envelope: dict[str, Any], verified_payload_bytes: bytes | None
    ) -> PreparedRecall:
        """Build the recall messages (or the removal) from the event's verified bytes; no database.

        Raises TerminalProcessingError for bytes that are not UTF-8 text, for an
        event with no producing actor and for a removal that does not match its
        causation event.
        """
        if is_removal(event_envelope):
            target, target_actor = removal_target(event_envelope, verified_payload_bytes)
            return PreparedRecall(target_actor, removal_target=target)
        event_id = event_envelope["event_id"]
        actor = actor_of(event_envelope)
        text, _, role_messages = event_text(event_envelope, verified_payload_bytes)
        parts = role_messages or [{"role": DEFAULT_ROLE, "content": text}]
        messages = tuple(
            Message(
                id=message_id(event_id, index),
                agent_id=actor,
                role=part["role"],
                text=part["content"],
                otid=event_id,
                sender_id=actor,
            )
            for index, part in enumerate(parts)
        )
        return PreparedRecall(actor, messages)

    def apply(
        self, native_tx_handle: duckdb.DuckDBPyConnection, prepared_work: PreparedRecall
    ) -> ProcessorResult:
        """Store or remove the recall messages on the owner's open transaction.

        Storing an event whose messages already exist reports
        processed_no_new_fact; removing an event that stored nothing does too.
        """
        store = self._store_for(native_tx_handle)
        store.ensure_schema()
        org = self.organization_id
        if prepared_work.removal_target is not None:
            stored = store.list_messages(
                org, prepared_work.agent_id, otid=prepared_work.removal_target
            )
            store.delete_messages(org, prepared_work.agent_id, [m.id for m in stored])
            changed = bool(stored)
        else:
            current = {
                m.id
                for m in store.list_messages(
                    org, prepared_work.agent_id, otid=prepared_work.messages[0].otid
                )
            }
            fresh = [m for m in prepared_work.messages if m.id not in current]
            if fresh:
                store.add_messages(org, fresh, actor_id=prepared_work.agent_id)
            changed = bool(fresh)
        status = "processed" if changed else "processed_no_new_fact"
        return ProcessorResult(
            status, PROCESSOR_ID, version("letta_memory_model"), PROJECTION_VERSION
        )

    # ---- read seam --------------------------------------------------------------

    async def prepare_query(self, scope: RetrievalScope, query: str, limit: int) -> dict[str, Any]:
        """Keep the query and limit, and fetch the question's numbers once per fresh model.

        Letta ranks recall by meaning when numbers exist: a model that is stale, or whose question
        numbers the supplier cannot give, is left out with its reason. When no model has numbers to
        rank with, retrieve answers with Letta's substring search as before. Memory never embeds the
        question itself.
        """
        vectors, left_out = await question_numbers_for_events(self._services, query)
        return {"query": query, "limit": limit, "vectors": vectors, "left_out": left_out}

    def _ranked_messages(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> TierRankings:
        """Rank the permitted events by each model's numbers and return each event's Letta messages.

        Per model the events come nearest first (cosine distance of that model's own numbers); each
        event contributes the recall messages Letta holds for it, in message order, every one carrying
        the event's distance. An event with no recall message is left out of the ranking. It never
        writes and never ranks across models.
        """
        store = self._store_for(native_handle) if _has_recall_schema(native_handle) else None
        rankings: dict[str, list[dict[str, Any]]] = {}
        ranked = rank_events(
            native_handle, scope, prepared_query["vectors"], prepared_query["limit"]
        )
        for label, events in ranked.items():
            hits: list[dict[str, Any]] = []
            for event in events:
                if store is None:
                    continue
                for message in store.list_messages(
                    self.organization_id,
                    event["actor_agent_id"],
                    otid=event["event_id"],
                    limit=prepared_query["limit"],
                ):
                    hits.append(
                        {
                            "event_id": event["event_id"],
                            "item_id": message.id,
                            "actor_agent_id": message.agent_id,
                            "message_id": message.id,
                            "role": message.role,
                            "text": message.text,
                            "sequence_id": message.sequence_id,
                            "distance": event["distance"],
                            "ranking": f"cosine distance of {label}'s own numbers for the event",
                        }
                    )
            rankings[label] = hits[: prepared_query["limit"]]
        return TierRankings(rankings, dict(prepared_query["left_out"]))

    def retrieve(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> list[dict[str, Any]] | TierRankings:
        """Return recall messages ranked by meaning when numbers exist, else by substring, newest first.

        With question numbers for at least one fresh model the answer is one ranking per model (see
        _ranked_messages). Without, it is Letta's recall search (case-insensitive substring of the
        whole query; an empty query matches everything), not semantic relevance: each hit says so in
        "ranking". Before anything was stored there is no Letta schema and the answer is no messages.
        Raises ValueError for an unknown visibility mode; it never writes.
        """
        if prepared_query.get("vectors"):
            return self._ranked_messages(native_handle, scope, prepared_query)
        visibility = scope.visibility
        if visibility.mode == "own":
            agents: list[str] | None = [scope.agent_id]
        elif visibility.mode == "granted":
            agents = sorted(visibility.actor_agent_ids)
        elif visibility.mode == "pool":
            agents = None
        else:
            raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")
        if agents == [] or not _has_recall_schema(native_handle):
            return []
        store = self._store_for(native_handle)
        org, limit = self.organization_id, prepared_query["limit"]
        if agents is None:
            agents = store.list_message_agent_ids(org)
        found = []
        for agent in agents:
            found.extend(
                store.list_messages(
                    org,
                    agent,
                    query_text=prepared_query["query"] or None,
                    limit=limit,
                    newest_first=True,
                )
            )
        found = sorted(
            (m for m in found if m.otid is not None),
            key=lambda m: m.sequence_id or 0,
            reverse=True,
        )[:limit]
        return [
            {
                "event_id": m.otid,
                "actor_agent_id": m.agent_id,
                "message_id": m.id,
                "role": m.role,
                "text": m.text,
                "sequence_id": m.sequence_id,
                "ranking": "case-insensitive substring match, newest first; not semantic relevance",
            }
            for m in found
        ]
