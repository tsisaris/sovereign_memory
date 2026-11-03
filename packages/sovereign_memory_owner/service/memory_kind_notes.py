"""Two kinds of memory for the personalization and relational tiers: pinned facts and reference passages.

It is for adding kinds to the two tiers that had none. A pinned fact (personalization tier, event type
"pinned_fact") is something the user wants always remembered, such as a preference; a reference passage
(relational tier, event type "reference_passage") is a passage of a document kept for lookup. Both take a
payload {"<type>": {"text": "...", "tags": ["..."]}}, are kept in a small table of their own in the
memory database (derived from the event, which stays the permanent record), have the supplier's numbers
owed for their text from every registered embedding model, and are found by meaning (one ranking per
model from that model's own numbers) or, with no numbers, by the words of the question. A tag filter and
a time window narrow them like every other hit. The settings name them (kinds.<name>.tier, .module,
.class); nothing here names a model, makes a number or shows an event the asker may not see.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_kind_notes.py | tagged notes in the personalization and relational tiers | Mem0 metadata-filtered memories (its own add path extracts facts by model and cannot store a fact as given), Cognee add with node_set tags (needs a Cognee dataset and cognify), Letta archival passages (episodic tier only, memory_kind_archival.py) | One small table per kind joined to the numbers of every model, the way the archival and summary kinds already are |
"""

import json
from typing import Any

import duckdb

from service.memory_event_ranking import permitted_producers
from service.memory_numbers_services import NumbersServices
from service.memory_processor_seam import RetrievalScope, TerminalProcessingError


class NotesKind:
    """The shared behaviour of a tagged-notes kind; a subclass names its tier, event type and table."""

    name = ""
    tier = ""
    event_type = ""
    payload_key = ""
    table = ""

    def claims(self, envelope: dict[str, Any]) -> bool:
        """Say whether an event is one of this kind: its type is the kind's event type."""
        return envelope.get("event_type") == self.event_type

    async def prepare(
        self, services: NumbersServices | None, envelope: dict[str, Any], payload: bytes | None
    ) -> tuple[str, str, tuple[str, ...]]:
        """Read the payload into (event id, text, tags) and owe the supplier's numbers for the text.

        Raises TerminalProcessingError when the payload is not the kind's form, because retrying the
        same bytes can never succeed. With no numbers services nothing is owed. It makes no number.
        """
        try:
            body = json.loads((payload or b"").decode("utf-8"))[self.payload_key]
            text, tags = body["text"], body.get("tags", [])
        except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise TerminalProcessingError(
                f'a {self.event_type} event needs the JSON {{"{self.payload_key}": '
                f'{{"text": ..., "tags": [...]}}}}: {error}'
            ) from error
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(tags, list)
            or any(not isinstance(tag, str) or not tag for tag in tags)
        ):
            raise TerminalProcessingError(
                f"a {self.event_type} event needs non-empty text and a list of non-empty tags"
            )
        event_id = envelope["event_id"]
        if services is not None:
            for model in await services.store.models("embedding"):
                await services.request_derived(
                    model.model_id,
                    model.revision,
                    event_id,
                    [text],
                    f"numbers for a {self.name} note",
                )
        return event_id, text, tuple(dict.fromkeys(tags))

    def _exists(self, handle: duckdb.DuckDBPyConnection) -> bool:
        """Say whether the kind's table exists yet; reads only."""
        found = handle.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
            "AND schema_name = 'main' AND table_name = ?",
            [self.table],
        ).fetchone()[0]
        return found == 1

    def apply(
        self,
        handle: duckdb.DuckDBPyConnection,
        organization_id: str,
        actor: str,
        event_id: str,
        work: tuple[str, str, tuple[str, ...]],
    ) -> bool:
        """Store the note on the owner's open transaction unless it is already there; True when added."""
        handle.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} (event_id VARCHAR PRIMARY KEY, "
            "actor_agent_id VARCHAR NOT NULL, text VARCHAR NOT NULL, tags VARCHAR NOT NULL)"
        )
        found = handle.execute(
            f"SELECT count(*) FROM {self.table} WHERE event_id = ?", [event_id]
        ).fetchone()[0]
        if found:
            return False
        handle.execute(
            f"INSERT INTO {self.table} VALUES (?, ?, ?, ?)",
            [event_id, actor, work[1], json.dumps(list(work[2]))],
        )
        return True

    def remove(
        self, handle: duckdb.DuckDBPyConnection, organization_id: str, target_event_id: str
    ) -> bool:
        """Delete the note an event stored (the event itself stays); True when one was deleted."""
        if not self._exists(handle):
            return False
        found = handle.execute(
            f"SELECT count(*) FROM {self.table} WHERE event_id = ?", [target_event_id]
        ).fetchone()[0]
        handle.execute(f"DELETE FROM {self.table} WHERE event_id = ?", [target_event_id])
        return bool(found)

    def _hit(self, row: tuple[Any, ...], distance: float | None, ranking: str) -> dict[str, Any]:
        """Shape one note row (event, actor, text, tags) as a search hit; pure."""
        return {
            "event_id": row[0],
            "item_id": f"{self.name}:{row[0]}",
            "note_id": f"{self.name}:{row[0]}",
            "note_kind": self.name,
            "actor_agent_id": row[1],
            "text": row[2],
            "tags": json.loads(row[3]),
            "distance": distance,
            "ranking": ranking,
        }

    def rank(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        vector_of_model: Any,
        label: str,
    ) -> list[dict[str, Any]]:
        """Return the permitted notes nearest the question by one model's numbers, nearest first.

        A note without numbers is not ranked. Only live events the scope's visibility permits count.
        """
        permitted = permitted_producers(scope)
        if not self._exists(handle) or permitted is None:
            return []
        model, vector = vector_of_model
        condition, params = permitted
        rows = handle.execute(
            f"SELECT k.event_id, k.actor_agent_id, k.text, k.tags, "
            f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
            f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
            "JOIN memory_events e ON e.event_id = p.event_id "
            f"JOIN {self.table} k ON k.event_id = p.event_id AND sha256(k.text) = p.text_fingerprint "
            "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
            "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
            "WHERE NOT n.stale AND p.source = 'derived' AND r.target_event_id IS NULL "
            f"AND x.piece_id IS NULL AND {condition} ORDER BY distance, k.event_id",
            [vector, *params],
        ).fetchall()
        return [
            self._hit(
                row, row[4], f"cosine distance of {label}'s own numbers for the {self.name} note"
            )
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
        """Return the permitted notes whose text holds the question's words, newest first; not relevance."""
        permitted = permitted_producers(scope)
        if not self._exists(handle) or permitted is None:
            return []
        condition, params = permitted
        rows = handle.execute(
            f"SELECT k.event_id, k.actor_agent_id, k.text, k.tags FROM {self.table} k "
            "JOIN memory_events e ON e.event_id = k.event_id "
            "LEFT JOIN memory_removals r ON r.target_event_id = e.event_id "
            f"WHERE r.target_event_id IS NULL AND {condition} "
            "AND lower(k.text) LIKE '%' || lower(?) || '%' ORDER BY e.created_at DESC, k.event_id LIMIT ?",
            [*params, query, limit],
        ).fetchall()
        return [
            self._hit(
                row, None, "case-insensitive substring match, newest first; not semantic relevance"
            )
            for row in rows
        ]


class PinnedFactKind(NotesKind):
    """Facts the user wants always remembered, in the personalization tier."""

    name = "pinned_facts"
    tier = "personalization"
    event_type = "pinned_fact"
    payload_key = "pinned_fact"
    table = "memory_kind_pinned_facts"


class ReferencePassageKind(NotesKind):
    """Passages of a document kept for lookup, in the relational tier."""

    name = "reference_passages"
    tier = "relational"
    event_type = "reference_passage"
    payload_key = "reference_passage"
    table = "memory_kind_reference_passages"
