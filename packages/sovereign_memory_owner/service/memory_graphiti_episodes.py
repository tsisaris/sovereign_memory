"""Graphiti episodes as the owner's "episodic" tier, written on the owner's own handle.

Every captured event becomes one Graphiti EpisodicNode, stored through the
maintained DuckDB driver's public episode operations on the owner's borrowed
native transaction handle, so the episode and its receipt commit together. The
episode's uuid is derived from the event id, so a replay overwrites the same
episode instead of adding another, and Graphiti's own add_episode(uuid=...) can
later extract facts onto exactly this episode. No model is called: this tier
stores and returns episodes; it extracts no entities or facts, which belong to
the temporal tier.
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any

import duckdb
from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_duckdb.duckdb_driver import DuckDBDriver

from service.memory_owner_core import REMOVAL_EVENT_TYPES, parse_instant
from service.memory_processor_seam import ProcessorResult, RetrievalScope, TerminalProcessingError

TIER_NAME = "episodic"
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


def group_of(actor: str) -> str:
    """Return the Graphiti group id holding one actor's episodes; pure.

    Graphiti accepts only letters, digits, dashes and underscores in a group
    id, so the actor is hashed rather than copied; the actor itself is kept in
    each episode's metadata.
    """
    return "actor-" + hashlib.sha256(actor.encode("utf-8")).hexdigest()


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
    """Owner processor and retriever for the "episodic" tier, backed by Graphiti episodes.

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
        if event_type in REMOVAL_EVENT_TYPES.values():
            return PreparedEpisode(
                None, self._removal_target(event_envelope, verified_payload_bytes)
            )
        actor = event_envelope.get("actor_agent_id", event_envelope.get("agent_id"))
        if not isinstance(actor, str) or not actor:
            raise TerminalProcessingError("Event has no producing actor")
        if event_envelope.get("schema_version") == "1.0.0":
            text, source = event_envelope["content"], EpisodeType.text
            created = event_envelope["timestamp"]
        else:
            text, source = self._text_of(verified_payload_bytes)
            created = event_envelope["created_at"]
        try:
            valid_at = parse_instant(created)
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

    @staticmethod
    def _text_of(payload: bytes | None) -> tuple[str, EpisodeType]:
        """Return the episode text and Graphiti source type of a version 2 payload.

        A JSON object carrying a list of {"role", "content"} messages becomes a
        message episode written as "role: content" lines, other JSON a json
        episode, and other UTF-8 a text episode. Raises TerminalProcessingError
        for missing or non-UTF-8 bytes.
        """
        if payload is None:
            raise TerminalProcessingError("Event has no stored payload bytes")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TerminalProcessingError(f"Payload is not UTF-8 text: {error}") from error
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return text, EpisodeType.text
        messages = body.get("messages") if isinstance(body, dict) else None
        if (
            isinstance(messages, list)
            and messages
            and all(
                isinstance(m, dict)
                and isinstance(m.get("role"), str)
                and isinstance(m.get("content"), str)
                for m in messages
            )
        ):
            return "\n".join(f"{m['role']}: {m['content']}" for m in messages), EpisodeType.message
        return text, EpisodeType.json

    @staticmethod
    def _removal_target(envelope: dict[str, Any], payload: bytes | None) -> str:
        """Return the event an owner-authored removal names; raise TerminalProcessingError if it disagrees."""
        try:
            body = json.loads((payload or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TerminalProcessingError(f"Removal payload is not JSON: {error}") from error
        target = body.get("target_event_id") if isinstance(body, dict) else None
        expected = (
            REMOVAL_EVENT_TYPES.get(body.get("operation")) if isinstance(body, dict) else None
        )
        if (
            not isinstance(target, str)
            or target != envelope.get("causation_event_id")
            or expected != envelope.get("event_type")
        ):
            raise TerminalProcessingError("Removal payload does not match its causation event")
        return target

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

    async def prepare_query(self, scope: RetrievalScope, query: str, limit: int) -> dict[str, Any]:
        """Fix the reference time and limit; this tier needs no model for its query."""
        return {"reference_time": datetime.now(timezone.utc), "limit": limit}

    def retrieve(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return the most recent episodes the scope's visibility permits, newest first.

        This is Graphiti's own recent-episode retrieval, not a relevance search:
        each hit says so in "ranking". Before anything was ever stored there is
        no Graphiti schema, and the answer is no episodes. Raises ValueError for
        an unknown visibility mode; it never writes.
        """
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
