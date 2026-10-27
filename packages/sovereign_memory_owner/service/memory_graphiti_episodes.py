"""Graphiti episodes in the owner's temporal tier, written on the owner's own handle.

Every captured event becomes one Graphiti EpisodicNode, stored through the
maintained DuckDB driver's public episode operations on the owner's borrowed
native transaction handle, so the episode and its receipt commit together. The
episode's uuid is derived from the event id, so a replay overwrites the same
episode instead of adding another, and Graphiti's own add_episode(uuid=...) can
later extract facts onto exactly this episode. No model is called: this tier
stores and returns episodes; temporal entity and fact extraction still requires
Graphiti's model pipeline.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any

import duckdb
from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_duckdb.duckdb_driver import DuckDBDriver

from service.memory_event_ranking import (
    permitted_producers,
    question_numbers_for_events,
    rank_events,
)
from service.memory_numbers_services import NumbersServices
from service.memory_owner_core import parse_instant
from service.memory_processor_seam import (
    ProcessorResult,
    RetrievalScope,
    TerminalProcessingError,
    TierRankings,
)
from service.memory_tier_support import (
    KIND_JSON,
    KIND_MESSAGES,
    actor_of,
    event_instant,
    event_text,
    group_of,
    is_removal,
    removal_target,
)

TIER_NAME = "temporal"
PROCESSOR_ID = "graphiti-duckdb-episodes"
PROJECTION_VERSION = "graphiti-episode-1"
EPISODE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "sovereign-memory-owner/episode")
PROVENANCE_FIELDS = (
    "schema_version",
    "event_type",
    "source_revision",
    "source_artifact_id",
    "work_id",
    "parent_event_id",
    "causation_event_id",
    "payload_sha256",
    "exactness",
)


def episode_uuid(event_id: str) -> str:
    """Return the deterministic Graphiti episode uuid of a captured event; pure."""
    return str(uuid.uuid5(EPISODE_NAMESPACE, event_id))


@dataclass(frozen=True)
class PreparedEpisode:
    """An episode to store, or (when removal is set) the episode of a removed event to delete.

    Holds plain data only, never a database handle.
    """

    episode: EpisodicNode | None
    removal_target: str | None = None


def _run(coroutine: Any) -> Any:
    """Run one Graphiti operation to completion on the calling worker thread.

    The DuckDB driver's operations are coroutines whose bodies execute
    synchronously on the given handle; the owner's worker thread has no event
    loop of its own, so each call gets a short-lived one.
    """
    return asyncio.run(coroutine)


def _has_graphiti_schema(handle: duckdb.DuckDBPyConnection) -> bool:
    """Say whether every object the Graphiti driver creates already exists; reads only.

    When they all exist, building a driver on the handle changes nothing, so a
    read can build one without writing.
    """
    tables = handle.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = 'graphiti' "
        "AND table_name IN ('graph_node', 'graph_edge', 'graph_fts_generations')"
    ).fetchone()[0]
    sequences = handle.execute(
        "SELECT count(*) FROM duckdb_sequences() WHERE database_name = current_database() "
        "AND schema_name = 'graphiti' AND sequence_name = 'graph_fts_gen_seq'"
    ).fetchone()[0]
    return tables == 3 and sequences == 1


class GraphitiEpisodeTier:
    """Owner processor and retriever for the temporal tier, backed by Graphiti episodes.

    It never opens, commits, rolls back or closes a connection; the driver it
    builds borrows the owner's handle for one call at a time.
    """

    tier_name = TIER_NAME

    # ---- write seam -------------------------------------------------------------

    async def prepare(
        self, event_envelope: dict[str, Any], verified_payload_bytes: bytes | None
    ) -> PreparedEpisode:
        """Build the episode (or the removal) from the event's verified bytes; no model, no database.

        Raises TerminalProcessingError for bytes that are not UTF-8 text, for a
        timestamp that is not ISO 8601, and for a removal payload that does not
        match its causation event.
        """
        event_id = event_envelope["event_id"]
        event_type = event_envelope.get("event_type", event_envelope.get("content_type"))
        if is_removal(event_envelope):
            target, _ = removal_target(event_envelope, verified_payload_bytes)
            return PreparedEpisode(None, target)
        actor = actor_of(event_envelope)
        text, kind, _ = event_text(event_envelope, verified_payload_bytes)
        source = {
            KIND_MESSAGES: EpisodeType.message,
            KIND_JSON: EpisodeType.json,
        }.get(kind, EpisodeType.text)
        try:
            valid_at = parse_instant(event_instant(event_envelope))
        except ValueError as error:
            raise TerminalProcessingError(f"Event time is not ISO 8601: {error}") from error
        metadata = {key: event_envelope.get(key) for key in PROVENANCE_FIELDS}
        metadata.update(event_id=event_id, actor_agent_id=actor)
        episode = EpisodicNode(
            uuid=episode_uuid(event_id),
            name=event_id,
            group_id=group_of(actor),
            labels=[],
            source=source,
            source_description=f"sovereign memory owner event ({event_type})",
            content=text,
            created_at=datetime.now(timezone.utc),
            valid_at=valid_at,
            episode_metadata=metadata,
        )
        return PreparedEpisode(episode)

    def apply(
        self, native_tx_handle: duckdb.DuckDBPyConnection, prepared_work: PreparedEpisode
    ) -> ProcessorResult:
        """Store or remove the episode on the owner's open transaction and report what happened.

        Removing an episode uses Graphiti's own removal, which also deletes the
        facts and singleton entities that episode created. A removal whose
        episode was never stored reports processed_no_new_fact.
        """
        driver = DuckDBDriver(connection=native_tx_handle)
        ops = driver.episode_node_ops
        if prepared_work.removal_target is not None:
            target = episode_uuid(prepared_work.removal_target)
            if _run(ops.get_by_uuids(driver, [target])):
                _run(ops.remove_episode_and_singletons(driver, target))
                status = "processed"
            else:
                status = "processed_no_new_fact"
        else:
            _run(ops.save(driver, prepared_work.episode))
            status = "processed"
        return ProcessorResult(status, PROCESSOR_ID, version("graphiti-duckdb"), PROJECTION_VERSION)

    # ---- read seam --------------------------------------------------------------

    _services: NumbersServices | None = None

    def attach_numbers(self, services: NumbersServices) -> None:
        """Receive the owner's numbers services, used only to get the numbers of a search question."""
        self._services = services

    def model_labels(self) -> list[str]:
        """Return the model labels a rebuild of this tier covers: none, as it holds no numbers."""
        return []

    async def prepare_query(self, scope: RetrievalScope, query: str, limit: int) -> dict[str, Any]:
        """Fix the reference time and limit, and fetch the question's numbers once per fresh model.

        A model that is stale, or whose question numbers the supplier cannot give, is left out with
        its reason. When no model has numbers to rank with, retrieve answers with the most recent
        episodes as before. Memory never embeds the question itself.
        """
        vectors, left_out = await question_numbers_for_events(self._services, query)
        return {
            "reference_time": datetime.now(timezone.utc),
            "limit": limit,
            "vectors": vectors,
            "left_out": left_out,
        }

    @staticmethod
    def _ranked_episodes(
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> TierRankings:
        """Rank the permitted events by each model's numbers and return each event's Graphiti episode.

        Per model the episodes come nearest first (cosine distance of that model's own numbers). An
        event whose episode Graphiti no longer holds is left out of the ranking. It never writes and
        never ranks across models.
        """
        driver = DuckDBDriver(connection=native_handle)
        ops = driver.episode_node_ops
        ranked = rank_events(
            native_handle, scope, prepared_query["vectors"], prepared_query["limit"]
        )
        rankings: dict[str, list[dict[str, Any]]] = {}
        for label, events in ranked.items():
            found = {
                episode.uuid: episode
                for episode in _run(
                    ops.get_by_uuids(driver, [episode_uuid(e["event_id"]) for e in events])
                )
            }
            hits = []
            for event in events:
                episode = found.get(episode_uuid(event["event_id"]))
                if episode is None:
                    continue
                metadata = episode.episode_metadata or {}
                hits.append(
                    {
                        "event_id": event["event_id"],
                        "item_id": episode.uuid,
                        "actor_agent_id": metadata.get("actor_agent_id"),
                        "episode_uuid": episode.uuid,
                        "content": episode.content,
                        "source": episode.source.value,
                        "valid_at": episode.valid_at.isoformat(),
                        "source_revision": metadata.get("source_revision"),
                        "distance": event["distance"],
                        "ranking": f"cosine distance of {label}'s own numbers for the event",
                    }
                )
            facts = GraphitiEpisodeTier._ranked_facts(
                native_handle, scope, prepared_query["vectors"][label], prepared_query["limit"]
            )
            rankings[label] = sorted(hits + facts, key=lambda hit: hit["distance"])[
                : prepared_query["limit"]
            ]
        return TierRankings(rankings, dict(prepared_query["left_out"]))

    @staticmethod
    def _ranked_facts(
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        vector_of_model: Any,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return the stored facts nearest the question by one model's numbers for the fact's words.

        The numbers are the derived pieces the supplier gave for each fact (keyed by the event the
        fact came from and the fingerprint of its words); a fact without numbers is not ranked. Each
        hit carries the fact, its four timestamps, the event it came from and the cosine distance.
        Only events the scope's visibility permits, and not removed, count. It never writes.
        """
        model, vector = vector_of_model
        permitted = permitted_producers(scope)
        if permitted is None:
            return []
        condition, params = permitted
        rows = native_handle.execute(
            "SELECT p.event_id, e.actor_agent_id, g.uuid, "
            "json_extract_string(g.properties, '$.fact'), "
            "json_extract_string(g.properties, '$.valid_at'), "
            "json_extract_string(g.properties, '$.invalid_at'), "
            "json_extract_string(g.properties, '$.expired_at'), "
            "json_extract_string(e.raw_event, '$.source_revision'), "
            f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
            f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
            "JOIN memory_events e ON e.event_id = p.event_id "
            "JOIN graphiti.graph_edge g ON sha256(json_extract_string(g.properties, '$.fact')) "
            "= p.text_fingerprint "
            "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
            "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
            "WHERE NOT n.stale AND p.source = 'derived' AND r.target_event_id IS NULL "
            f"AND x.piece_id IS NULL AND {condition} ORDER BY distance, g.uuid",
            [vector, *params],
        ).fetchall()
        facts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for (
            event_id,
            actor,
            uuid_,
            fact,
            valid_at,
            invalid_at,
            expired_at,
            revision,
            distance,
        ) in rows:
            if uuid_ in seen:
                continue
            seen.add(uuid_)
            facts.append(
                {
                    "event_id": event_id,
                    "item_id": uuid_,
                    "actor_agent_id": actor,
                    "fact": fact,
                    "text": fact,
                    "valid_at": valid_at,
                    "invalid_at": invalid_at,
                    "expired_at": expired_at,
                    "source_revision": revision,
                    "distance": distance,
                    "ranking": f"cosine distance of {model.label}'s own numbers for the fact",
                }
            )
        return facts[:limit]

    def retrieve(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> list[dict[str, Any]] | TierRankings:
        """Return episodes ranked by meaning when numbers exist, else the most recent, newest first.

        With question numbers for at least one fresh model the answer is one ranking per model (see
        _ranked_episodes). Without, it is Graphiti's own recent-episode retrieval, not a relevance
        search: each hit says so in "ranking". Before anything was ever stored there is no Graphiti
        schema, and the answer is no episodes. Raises ValueError for an unknown visibility mode; it
        never writes.
        """
        if prepared_query.get("vectors") and _has_graphiti_schema(native_handle):
            return self._ranked_episodes(native_handle, scope, prepared_query)
        visibility = scope.visibility
        if visibility.mode == "own":
            groups: list[str] | None = [group_of(scope.agent_id)]
        elif visibility.mode == "granted":
            groups = [group_of(actor) for actor in sorted(visibility.actor_agent_ids)]
        elif visibility.mode == "pool":
            groups = None
        else:
            raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")
        if groups == [] or not _has_graphiti_schema(native_handle):
            return []
        driver = DuckDBDriver(connection=native_handle)
        episodes = _run(
            driver.episode_node_ops.retrieve_episodes(
                driver,
                prepared_query["reference_time"],
                last_n=prepared_query["limit"],
                group_ids=groups,
            )
        )
        hits = []
        for episode in episodes:
            metadata = episode.episode_metadata or {}
            hits.append(
                {
                    "event_id": metadata.get("event_id"),
                    "actor_agent_id": metadata.get("actor_agent_id"),
                    "episode_uuid": episode.uuid,
                    "content": episode.content,
                    "source": episode.source.value,
                    "valid_at": episode.valid_at.isoformat(),
                    "source_revision": metadata.get("source_revision"),
                    "ranking": "most recent episodes, not query relevance",
                }
            )
        return hits
