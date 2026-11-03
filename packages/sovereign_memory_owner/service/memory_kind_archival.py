"""Letta's archival memory as a kind of the episodic tier: insert with tags, search by meaning, tags and dates.

It is for Letta's archival_memory_insert and archival_memory_search. An event of type "archival_memory"
whose payload is {"archival_memory": {"text": "...", "tags": ["..."]}} is claimed by this kind: its text
is stored as an archival passage with its tags in the actor's own archive through Letta's own store, and
the supplier's numbers for the text are owed from every registered embedding model. A search ranks the
passages one ranking per model from that model's own numbers (the passage text joined to the numbers
by the event it came from and the fingerprint of its words); without numbers a passage is found by the
words of the question. It takes the settings-named tier "episodic", never names a model, makes no number,
and returns only passages of live events the asker may see.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_kind_archival.py | Letta archival memory with tags | Letta LettaMemoryStore.insert_archival_passage and search_archival_passages (reused for storage; its search compares one stored vector, so it cannot rank by every model), Mem0 vector search with metadata filters, Graphiti search filters | Storage is Letta's own code; ranking joins the passages to the numbers of every registered model, as the facts and memories of the other tiers already are |
"""

import json
import uuid
from typing import Any

import duckdb
from letta_memory_model.letta_duckdb_store import LettaMemoryStore, LettaNotFound
from letta_memory_model.schemas.passage import Passage

from service.memory_event_ranking import permitted_producers
from service.memory_numbers_services import NumbersServices
from service.memory_processor_seam import RetrievalScope, TerminalProcessingError

PASSAGE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "sovereign-memory-owner/letta-passage")
EVENT_TYPE = "archival_memory"
PAYLOAD_KEY = "archival_memory"
REASON = "numbers for a Letta archival passage"


def passage_id(event_id: str) -> str:
    """Return the deterministic Letta archival passage id of an archival event; pure."""
    return "passage-" + str(uuid.uuid5(PASSAGE_NAMESPACE, event_id))


def archive_id(actor: str) -> str:
    """Return the id of the archival memory (Letta's archive) one actor agent owns; pure."""
    return "archive-" + str(uuid.uuid5(PASSAGE_NAMESPACE, f"archive:{actor}"))


def _has_passage_schema(handle: duckdb.DuckDBPyConnection) -> bool:
    """Say whether Letta's archival passages table exists yet; reads only."""
    found = handle.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = 'letta' AND table_name = 'archival_passages'"
    ).fetchone()[0]
    return found == 1


def _hit(row: tuple[Any, ...], distance: float | None, ranking: str) -> dict[str, Any]:
    """Shape one archival passage row (event, actor, id, text, tags) as a search hit; pure."""
    event_id, actor, identifier, text, tags = row[:5]
    return {
        "event_id": event_id,
        "item_id": identifier,
        "passage_id": identifier,
        "actor_agent_id": actor,
        "text": text,
        "tags": json.loads(tags) if isinstance(tags, str) else list(tags or []),
        "distance": distance,
        "ranking": ranking,
    }


class ArchivalKind:
    """The archival-passage kind of the episodic tier; see the module words."""

    name = "letta_archival"
    tier = "episodic"

    def claims(self, envelope: dict[str, Any]) -> bool:
        """Say whether an event is an archival insert: its type is "archival_memory"."""
        return envelope.get("event_type") == EVENT_TYPE

    async def prepare(
        self, services: NumbersServices | None, envelope: dict[str, Any], payload: bytes | None
    ) -> tuple[str, str, tuple[str, ...]]:
        """Read the insert into (event id, text, tags) and owe the supplier's numbers for the text.

        Raises TerminalProcessingError when the payload is not the archival form, because retrying
        the same bytes can never succeed. With no numbers services nothing is owed and the passage
        is found by words only. It makes no number.
        """
        try:
            body = json.loads((payload or b"").decode("utf-8"))[PAYLOAD_KEY]
            text, tags = body["text"], body.get("tags", [])
        except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise TerminalProcessingError(
                "an archival_memory event needs the JSON "
                f'{{"archival_memory": {{"text": ..., "tags": [...]}}}}: {error}'
            ) from error
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(tags, list)
            or any(not isinstance(tag, str) or not tag for tag in tags)
        ):
            raise TerminalProcessingError(
                "an archival_memory event needs non-empty text and a list of non-empty tags"
            )
        event_id = envelope["event_id"]
        if services is not None:
            for model in await services.store.models("embedding"):
                await services.request_derived(
                    model.model_id, model.revision, event_id, [text], REASON
                )
        return event_id, text, tuple(dict.fromkeys(tags))

    def apply(
        self,
        store: LettaMemoryStore,
        organization_id: str,
        actor: str,
        event_id: str,
        work: tuple[str, str, tuple[str, ...]],
    ) -> bool:
        """Store the passage in the actor's archive unless it is already there; True when it was added."""
        _, text, tags = work
        try:
            store.get_archival_passage(organization_id, passage_id(event_id))
            return False
        except LettaNotFound:
            store.insert_archival_passage(
                organization_id,
                archive_id(actor),
                Passage(
                    id=passage_id(event_id),
                    text=text,
                    tags=list(tags),
                    metadata={"event_id": event_id, "actor_agent_id": actor},
                ),
                actor_id=actor,
            )
            return True

    def remove(self, store: LettaMemoryStore, organization_id: str, target_event_id: str) -> bool:
        """Delete the passage an event stored, if there is one; True when one was deleted."""
        try:
            store.delete_archival_passage(organization_id, passage_id(target_event_id))
            return True
        except LettaNotFound:
            return False

    def rank(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        vector_of_model: Any,
        label: str,
    ) -> list[dict[str, Any]]:
        """Return the passages nearest the question by one model's numbers, nearest first.

        A passage without numbers is not ranked. Only live events the scope's visibility permits
        count. Each hit carries the text, its tags, its event and the cosine distance. It never writes.
        """
        if not _has_passage_schema(handle):
            return []
        model, vector = vector_of_model
        permitted = permitted_producers(scope)
        if permitted is None:
            return []
        condition, params = permitted
        rows = handle.execute(
            "SELECT p.event_id, e.actor_agent_id, a.id, a.text, CAST(a.tags AS VARCHAR), "
            f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
            f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
            "JOIN memory_events e ON e.event_id = p.event_id "
            "JOIN letta.archival_passages a ON json_extract_string(a.metadata_, '$.event_id') "
            "= p.event_id AND sha256(a.text) = p.text_fingerprint "
            "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
            "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
            "WHERE NOT n.stale AND p.source = 'derived' AND NOT a.is_deleted "
            "AND a.organization_id = ? AND r.target_event_id IS NULL AND x.piece_id IS NULL "
            f"AND {condition} ORDER BY distance, a.id",
            [vector, organization_id, *params],
        ).fetchall()
        return [
            _hit(row, row[5], f"cosine distance of {label}'s own numbers for the archival passage")
            for row in rows
        ]

    def find(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return the permitted passages whose text holds the question's words, newest first.

        A case-insensitive substring match (an empty question matches all), not relevance. Only live
        events the scope permits count. It never writes.
        """
        if not _has_passage_schema(handle):
            return []
        permitted = permitted_producers(scope)
        if permitted is None:
            return []
        condition, params = permitted
        rows = handle.execute(
            "SELECT json_extract_string(a.metadata_, '$.event_id'), e.actor_agent_id, a.id, a.text, "
            "CAST(a.tags AS VARCHAR) FROM letta.archival_passages a "
            "JOIN memory_events e ON e.event_id = json_extract_string(a.metadata_, '$.event_id') "
            "LEFT JOIN memory_removals r ON r.target_event_id = e.event_id "
            "WHERE NOT a.is_deleted AND a.organization_id = ? AND r.target_event_id IS NULL "
            f"AND {condition} AND lower(a.text) LIKE '%' || lower(?) || '%' "
            "ORDER BY a.created_at DESC, a.id LIMIT ?",
            [organization_id, *params, query, limit],
        ).fetchall()
        return [
            _hit(
                row, None, "case-insensitive substring match, newest first; not semantic relevance"
            )
            for row in rows
        ]
