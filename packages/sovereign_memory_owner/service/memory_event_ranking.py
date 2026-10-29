"""Rank captured events by a model's own numbers, for the tiers that keep an event's content themselves.

The Letta recall tier and the Graphiti episode tier hold each event's messages or episode in their own
stores. This module ranks the events: for every embedding model fresh for whole-event pieces, it orders
the events the scope's visibility permits by the cosine distance between the question's numbers and that
model's numbers for the event, nearest first, one entry per event. The caller then reads its own content
for the ranked events. Nothing here makes a number, runs a model or writes; a model whose numbers or
question numbers are missing is left out with its reason and is never an empty ranking.
"""

from typing import Any

import duckdb

from service.memory_numbers_services import NumbersServices
from service.memory_processor_seam import RetrievalScope


async def question_numbers_for_events(
    services: NumbersServices | None, query: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch the question's numbers from the supplier, once per model fresh for whole-event pieces.

    Goes in: the numbers services (None when the tier was built without them) and the question. Comes
    out: ({label: (model, numbers)}, {label: reason} for each model left out). With no services both are
    empty, so the caller answers as it did before it had numbers. It never embeds the question itself.
    """
    if services is None:
        return {}, {}
    return await services.question_vectors("event", query)


def permitted_producers(scope: RetrievalScope) -> tuple[str, list[Any]] | None:
    """Return (SQL condition on events.actor_agent_id, parameters) for the scope, or None for no producer.

    Raises ValueError for a visibility mode it does not know.
    """
    visibility = scope.visibility
    if visibility.mode == "own":
        return "e.actor_agent_id = ?", [scope.agent_id]
    if visibility.mode == "granted":
        actors = sorted(visibility.actor_agent_ids)
        return ("e.actor_agent_id IN (SELECT unnest(?))", [actors]) if actors else None
    if visibility.mode == "pool":
        return "TRUE", []
    raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")


def rank_events(
    handle: duckdb.DuckDBPyConnection,
    scope: RetrievalScope,
    vectors: dict[str, Any],
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Return, per model label, the permitted live events nearest the question, one row per event.

    Each row holds event_id, actor_agent_id, source_revision, the nearest piece's id and text, and the
    cosine distance of that model's own numbers (lower is nearer). Events that were removed, whose
    pieces were retired, or whose numbers are stale are not ranked. Reads only.
    """
    permitted = permitted_producers(scope)
    if permitted is None:
        return {label: [] for label in vectors}
    condition, params = permitted
    rankings: dict[str, list[dict[str, Any]]] = {}
    for label, (model, vector) in vectors.items():
        rows = handle.execute(
            f"SELECT n.event_id, e.actor_agent_id, json_extract_string(e.raw_event, '$.source_revision'), "
            f"n.piece_id, p.text, "
            f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
            f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
            "JOIN memory_events e ON e.event_id = n.event_id "
            "LEFT JOIN memory_removals r ON r.target_event_id = n.event_id "
            "LEFT JOIN memory_piece_retirements x ON x.piece_id = n.piece_id "
            "WHERE NOT n.stale AND p.source = 'event' AND r.target_event_id IS NULL "
            f"AND x.piece_id IS NULL AND {condition} ORDER BY distance, n.piece_id",
            [vector, *params],
        ).fetchall()
        seen: set[str] = set()
        ranked: list[dict[str, Any]] = []
        for event_id, actor, revision, piece_id, text, distance in rows:
            if event_id in seen:
                continue
            seen.add(event_id)
            ranked.append(
                {
                    "event_id": event_id,
                    "actor_agent_id": actor,
                    "source_revision": revision,
                    "piece_id": piece_id,
                    "piece_text": text,
                    "distance": distance,
                }
            )
        rankings[label] = ranked[:limit]
    return rankings
