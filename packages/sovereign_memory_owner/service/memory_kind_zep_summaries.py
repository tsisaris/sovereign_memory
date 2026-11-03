"""Zep's thread and user summaries as a kind of the temporal tier, found by meaning (Zep's summary embedding).

It is for searching the summaries the Zep part writes. A summary is a session's newest thread summary or
a user's summary; when it is made with the id of the stored event the conversation came from, the
supplier's numbers for its words are owed from every registered embedding model and kept as a derived
piece of that event. This kind ranks the summaries one ranking per model from that model's own numbers
(the summary joined to the numbers by the event and the fingerprint of its words); without numbers a
summary is found by the words of the question. It takes the settings-named tier "temporal", stores
nothing itself (the Zep part does), never names a model, makes no number, and returns only summaries
whose event is live and visible to the asker.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_kind_zep_summaries.py | Zep summary embedding and search | Zep CE message-summary embedding (discontinued server, Go), Zep Cloud summary search (hosted, closed), the derived-pieces mechanism already used for Graphiti facts and Mem0 memories | The existing derived-pieces mechanism reused; no second store of numbers |
"""

from typing import Any

import duckdb

from service.memory_event_ranking import permitted_producers
from service.memory_processor_seam import RetrievalScope

TABLES = ("summaries", "user_summaries")


def _current_summaries(handle: duckdb.DuckDBPyConnection) -> str | None:
    """Return the SQL of Zep's current summaries (key, kind, owner, content), or None when it has none yet.

    A session's current summary is its newest undeleted thread summary; a user has one summary row.
    """
    present = {
        row[0]
        for row in handle.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database() "
            "AND schema_name = 'zep_ce' AND table_name IN ('summaries', 'user_summaries')"
        ).fetchall()
    }
    parts: list[str] = []
    if "summaries" in present:
        parts.append(
            "SELECT content, 'thread' AS kind, session_id AS owner, CAST(uuid AS VARCHAR) AS key "
            "FROM zep_ce.summaries WHERE deleted_at IS NULL "
            "QUALIFY row_number() OVER (PARTITION BY session_id ORDER BY created_at DESC, id DESC) = 1"
        )
    if "user_summaries" in present:
        parts.append(
            "SELECT content, 'user' AS kind, user_id AS owner, 'user-summary:' || user_id AS key "
            "FROM zep_ce.user_summaries"
        )
    return " UNION ALL ".join(parts) if parts else None


def _hit(row: tuple[Any, ...], distance: float | None, ranking: str) -> dict[str, Any]:
    """Shape one summary row (event, actor, key, content, kind, owner) as a search hit; pure."""
    event_id, actor, key, content, kind, owner = row[:6]
    return {
        "event_id": event_id,
        "item_id": key,
        "summary_id": key,
        "actor_agent_id": actor,
        "text": content,
        "summary_kind": kind,
        "summary_of": owner,
        "distance": distance,
        "ranking": ranking,
    }


class ZepSummaryKind:
    """The summary kind of the temporal tier; see the module words."""

    name = "zep_summaries"
    tier = "temporal"

    def rank(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        vector_of_model: Any,
        label: str,
    ) -> list[dict[str, Any]]:
        """Return Zep's current summaries nearest the question by one model's numbers, nearest first.

        A summary made without an event, or whose numbers are still owed, is not ranked. Only live
        events the scope's visibility permits count. Each hit carries the summary, whose it is and the
        cosine distance. It never writes.
        """
        current = _current_summaries(handle)
        permitted = permitted_producers(scope)
        if current is None or permitted is None:
            return []
        model, vector = vector_of_model
        condition, params = permitted
        rows = handle.execute(
            "SELECT p.event_id, e.actor_agent_id, s.key, s.content, s.kind, s.owner, "
            f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
            f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
            "JOIN memory_events e ON e.event_id = p.event_id "
            f"JOIN ({current}) s ON sha256(s.content) = p.text_fingerprint "
            "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
            "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
            "WHERE NOT n.stale AND p.source = 'derived' AND r.target_event_id IS NULL "
            f"AND x.piece_id IS NULL AND {condition} ORDER BY distance, s.key",
            [vector, *params],
        ).fetchall()
        seen: set[str] = set()
        hits: list[dict[str, Any]] = []
        for row in rows:
            if row[2] in seen:
                continue
            seen.add(row[2])
            hits.append(
                _hit(row, row[6], f"cosine distance of {label}'s own numbers for the Zep summary")
            )
        return hits

    def find(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return the current summaries whose text holds the question's words, for when no model has numbers.

        A summary counts only when it was made with an event (a derived piece ties it to one) that is
        live and visible. A case-insensitive substring match, not relevance. It never writes.
        """
        current = _current_summaries(handle)
        permitted = permitted_producers(scope)
        if current is None or permitted is None:
            return []
        condition, params = permitted
        rows = handle.execute(
            "SELECT DISTINCT ON (s.key) p.event_id, e.actor_agent_id, s.key, s.content, s.kind, s.owner "
            "FROM memory_pieces p JOIN memory_events e ON e.event_id = p.event_id "
            f"JOIN ({current}) s ON sha256(s.content) = p.text_fingerprint "
            "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
            "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
            "WHERE p.source = 'derived' AND r.target_event_id IS NULL AND x.piece_id IS NULL "
            f"AND {condition} AND lower(s.content) LIKE '%' || lower(?) || '%' "
            "ORDER BY s.key LIMIT ?",
            [*params, query, limit],
        ).fetchall()
        return [
            _hit(row, None, "case-insensitive substring match; not semantic relevance")
            for row in rows
        ]
