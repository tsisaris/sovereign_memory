"""Graphiti's language-model steps inside the memory service, made from the language_model setting.

It is for turning a stored episode into entities, dated facts, contradiction decisions and
summaries, and a group of entities into named communities, with the model the settings name.
It takes the validated language_model settings and a way to reach the database, and answers
the steps "ingest" and "communities". It never makes or asks for numbers (so the steps of
Graphiti that rank by similarity are replaced by name-word overlap and by shared entities,
and the reply says so), and it never holds a database connection across a model call.
"""

import asyncio
import os
from datetime import datetime
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

import duckdb
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.edges import EntityEdge, HasEpisodeEdge
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.graphiti_types import GraphitiClients, uses_prompt_routing
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EntityNode, EpisodicNode, SagaNode
from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT
from graphiti_core.telemetry.telemetry import TELEMETRY_ENV_VAR
from graphiti_core.tracer import NoOpTracer
from graphiti_core.utils.bulk_utils import resolve_edge_pointers
from graphiti_core.utils.datetime_utils import utc_now
from graphiti_core.utils.maintenance.community_operations import (
    build_community,
    get_community_clusters,
)
from graphiti_core.utils.maintenance.dedup_helpers import (
    DedupResolutionState,
    _build_candidate_indexes,
    _resolve_with_identity_properties,
    _resolve_with_similarity,
)
from graphiti_core.utils.maintenance.edge_operations import (
    _apply_temporal_invalidation,
    _dedupe_extracted_edge,
    _same_pair_edges,
    build_episodic_edges,
    extract_edges,
)
from graphiti_core.utils.maintenance.node_operations import (
    NODE_DEDUP_CANDIDATE_LIMIT,
    _build_edges_by_node,
    _commit_resolution,
    _extract_entity_summaries_batch,
    _merge_candidate_nodes,
    _resolve_with_llm,
    extract_nodes,
)
from graphiti_duckdb.duckdb_driver import DuckDBDriver

from service.memory_graphiti_episodes import episode_uuid
from service.memory_model_parts import AchievedRecorder, DatabaseAccess, build_door

CANDIDATE_MODES = ("shared_entities", "whole_group")
SUMMARY_MODES = ("graphiti_default", "always_model")
NO_NUMBERS = (
    "numbers not yet supplied: memory never makes numbers, so this Graphiti step that ranks by "
    "similarity cannot run here"
)


class NoNumbersEmbedder(EmbedderClient):
    """An embedder that refuses, because memory never makes numbers.

    It is for filling the embedder slot Graphiti requires. It takes text and gives back only
    the plain refusal. It never returns a vector, a zero vector or any invented number.
    """

    async def create(self, input_data: Any) -> list[float]:
        """Refuse with the plain words that numbers have not been supplied."""
        raise RuntimeError(NO_NUMBERS)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """Refuse with the plain words that numbers have not been supplied."""
        raise RuntimeError(NO_NUMBERS)


class NoNumbersCrossEncoder(CrossEncoderClient):
    """A reranker slot that refuses, because no reranking model is supplied to memory.

    It is for filling the cross-encoder slot Graphiti requires. It takes a query and passages and
    gives back only the plain refusal. It never returns a score.
    """

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        """Refuse with the plain words that numbers have not been supplied."""
        raise RuntimeError(NO_NUMBERS)


def _drive(coroutine: Any) -> Any:
    """Run one Graphiti database operation to completion on the calling worker thread.

    The DuckDB driver's operations are coroutines whose bodies run synchronously on the handle
    they are given, and the worker thread has no event loop, so each call gets a short one. It
    takes the coroutine and returns its result. It never touches a model.
    """
    return asyncio.run(coroutine)


def _words(text: str) -> set[str]:
    """Return the lower-case words of a name, used to order candidate entities by shared words."""
    return set(text.lower().split())


class GraphitiModel:
    """The Graphiti part: ingest an episode into the graph, and build communities."""

    name = "graphiti"

    def __init__(self, language_model: Mapping[str, Any], database: DatabaseAccess) -> None:
        """Keep the settings and the database access, and build the one HTTP door that records answers."""
        self.language_model = dict(language_model)
        self.database = database
        self.recorder = AchievedRecorder()
        self.door = build_door(self.language_model, self.recorder)
        self.unused = duckdb.connect(":memory:")

    def steps(self) -> tuple[str, ...]:
        """Return the step names this part answers."""
        return ("ingest", "communities", "saga_summary")

    def achieved_model(self) -> str | None:
        """Return the model name the server reported for the latest answer, or None before any."""
        return self.recorder.last_model

    def _clients(self, model: str) -> GraphitiClients:
        """Build Graphiti's client bundle for one model, from the settings and nothing else.

        The embedder and reranker slots refuse; the database slot holds an empty private
        database that the steps never use, because every read and write goes through the
        service's own access.
        """
        options = self.language_model["options"]
        config = LLMConfig(
            api_key=self.language_model["api_key"],
            model=model,
            small_model=model,
            base_url=self.language_model["base_url"].rstrip("/") + "/v1",
            **{key: options[key] for key in ("temperature", "max_tokens") if key in options},
        )
        llm_client = OpenAIGenericClient(config=config, client=self.door)
        return GraphitiClients(
            driver=DuckDBDriver(connection=self.unused),
            llm_client=llm_client,
            embedder=NoNumbersEmbedder(),
            cross_encoder=NoNumbersCrossEncoder(),
            tracer=NoOpTracer(),
        )

    async def run_step(self, step: str, args: dict[str, Any], model: str) -> dict[str, Any]:
        """Run "ingest" (event_ids, candidates) or "communities" (group_id) with the named model."""
        clients = self._clients(model)
        if step == "ingest":
            event_ids = args.get("event_ids")
            mode = args.get("candidates", CANDIDATE_MODES[0])
            if not isinstance(event_ids, list) or not event_ids:
                raise ValueError("ingest needs event_ids, a non-empty list")
            if mode not in CANDIDATE_MODES:
                raise ValueError(f"candidates must be one of {', '.join(CANDIDATE_MODES)}")
            summaries = args.get("summaries", SUMMARY_MODES[0])
            if summaries not in SUMMARY_MODES:
                raise ValueError(f"summaries must be one of {', '.join(SUMMARY_MODES)}")
            return {
                "episodes": [
                    await self._ingest(clients, e, mode, summaries == SUMMARY_MODES[1])
                    for e in event_ids
                ]
            }
        group_id = args.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("this step needs a group_id")
        if step == "saga_summary":
            return await self._saga_summary(clients, group_id, args)
        return await self._communities(clients, group_id)

    async def _saga_summary(
        self, clients: GraphitiClients, group_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Group stored episodes into a saga (when given) and have Graphiti's own code summarise it.

        It takes the group id, saga_name and optionally event_ids to link into the saga. The saga
        and its HAS_EPISODE links are created by the service because Graphiti makes them only inside
        add_episode, which needs numbers. The summary itself is Graphiti.summarize_saga run
        unchanged on a connection of its own, with the model from the settings. It returns the
        saga's name, summary and how many episodes were read; it stores the summary on the saga.
        """
        name, event_ids = args.get("saga_name"), args.get("event_ids", [])
        if not isinstance(name, str) or not name or not isinstance(event_ids, list):
            raise ValueError("saga_summary needs saga_name and, optionally, event_ids (a list)")

        def seed(handle: Any) -> str:
            """Create the saga and link the named episodes to it; no model."""
            driver = DuckDBDriver(connection=handle)
            saga = _drive(driver.saga_node_ops.get_by_name(driver, name, group_id))
            if saga is None:
                saga = SagaNode(name=name, group_id=group_id)
                _drive(driver.saga_node_ops.save(driver, saga))
            for event_id in event_ids:
                target = episode_uuid(event_id)
                if not _drive(driver.episode_node_ops.get_by_uuids(driver, [target])):
                    raise ValueError(f"event {event_id} has no episode in the graph yet")
                link = HasEpisodeEdge(
                    uuid=str(uuid5(NAMESPACE_URL, f"saga-link/{saga.uuid}/{target}")),
                    source_node_uuid=saga.uuid,
                    target_node_uuid=target,
                    group_id=group_id,
                    created_at=utc_now(),
                )
                _drive(driver.has_episode_edge_ops.save(driver, link))
            return saga.uuid

        saga_uuid = await self.database.write(seed)
        os.environ[TELEMETRY_ENV_VAR] = "false"
        client = Graphiti(
            graph_driver=DuckDBDriver(db=str(self.database.path)),
            llm_client=clients.llm_client,
            embedder=NoNumbersEmbedder(),
            cross_encoder=NoNumbersCrossEncoder(),
        )
        try:
            saga = await client.summarize_saga(saga_uuid)
        finally:
            await client.driver.close()
        return {
            "saga": saga.name,
            "summary": saga.summary,
            "episodes_read": saga._summary_episodes_selected,
        }

    @staticmethod
    def _load(handle: Any, event_id: str) -> dict[str, Any]:
        """Read one episode, the episodes before it and the group's entities and facts; no model."""
        driver = DuckDBDriver(connection=handle)
        found = _drive(driver.episode_node_ops.get_by_uuids(driver, [episode_uuid(event_id)]))
        if not found:
            raise ValueError(f"event {event_id} has no episode in the graph yet")
        episode = found[0]
        previous = _drive(
            driver.episode_node_ops.retrieve_episodes(
                driver, episode.valid_at, last_n=RELEVANT_SCHEMA_LIMIT, group_ids=[episode.group_id]
            )
        )
        return {
            "episode": episode,
            "previous": [p for p in previous if p.uuid != episode.uuid],
            "nodes": _drive(driver.entity_node_ops.get_by_group_ids(driver, [episode.group_id])),
            "edges": _drive(driver.entity_edge_ops.get_by_group_ids(driver, [episode.group_id])),
        }

    @staticmethod
    def _candidates_for(node: EntityNode, existing: list[EntityNode]) -> list[EntityNode]:
        """Order the group's entities by words shared with the node's name and keep Graphiti's own limit.

        This stands in for Graphiti's search by similarity of numbers, which needs numbers memory
        does not make. It takes the new node and the existing entities. It returns up to
        NODE_DEDUP_CANDIDATE_LIMIT existing entities that share at least one name word, the
        most shared first. It never changes a node.
        """
        wanted = _words(node.name)
        scored = [(len(wanted & _words(other.name)), other) for other in existing]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [other for _, other in scored[:NODE_DEDUP_CANDIDATE_LIMIT]]

    async def _resolve_nodes(
        self,
        clients: GraphitiClients,
        extracted: list[EntityNode],
        episode: EpisodicNode,
        previous: list[EpisodicNode],
        existing: list[EntityNode],
    ) -> tuple[list[EntityNode], dict[str, str]]:
        """Decide which extracted entities are already known, translated from resolve_extracted_nodes.

        Original: graphiti_core/utils/maintenance/node_operations.py, resolve_extracted_nodes.
        Only the candidate search differs (word overlap instead of numbers). It takes the clients,
        the extracted entities, the episode, the earlier episodes and the group's entities, and
        returns the resolved entities with a map from each extracted uuid to its resolved uuid.
        """
        candidates_by_node = [self._candidates_for(node, existing) for node in extracted]
        state = DedupResolutionState(
            resolved_nodes=[None] * len(extracted), uuid_map={}, unresolved_indices=[]
        )
        for idx, (node, candidates) in enumerate(zip(extracted, candidates_by_node, strict=True)):
            if not candidates:
                continue
            indexes = _build_candidate_indexes(candidates)
            local = DedupResolutionState(resolved_nodes=[None], uuid_map={}, unresolved_indices=[])
            _resolve_with_identity_properties([node], indexes, local, None)
            if local.resolved_nodes[0] is None:
                _resolve_with_similarity([node], indexes, local)
            if local.resolved_nodes[0] is not None:
                _commit_resolution(
                    state, local.resolved_nodes[0], local.uuid_map, local.duplicate_pairs, idx
                )
                continue
            state.unresolved_indices.append(idx)
        if state.unresolved_indices:
            pooled = _merge_candidate_nodes(
                [c for idx in state.unresolved_indices for c in candidates_by_node[idx]]
            )
            await _resolve_with_llm(
                clients.llm_client,
                extracted,
                _build_candidate_indexes(pooled),
                state,
                episode,
                previous,
                None,
                clients=clients,
            )
        for idx, node in enumerate(extracted):
            if state.resolved_nodes[idx] is None:
                state.resolved_nodes[idx] = node
                state.uuid_map[node.uuid] = node.uuid
        return [n for n in state.resolved_nodes if n is not None], state.uuid_map

    async def _resolve_edges(
        self,
        clients: GraphitiClients,
        edges: list[EntityEdge],
        episode: EpisodicNode,
        existing_edges: list[EntityEdge],
        mode: str,
    ) -> tuple[list[EntityEdge], list[EntityEdge], list[EntityEdge]]:
        """Date each fact and let the model decide which known facts it contradicts.

        Original: graphiti_core/utils/maintenance/edge_operations.py, resolve_extracted_edges. The
        searches by number are replaced: same-pair facts come from the stored facts between the
        two entities, and the facts that might be contradicted are, in mode "shared_entities", the
        stored facts touching either entity, or, in mode "whole_group", every stored fact. It
        returns the resolved facts, the facts it ended and the facts new to the graph.
        """
        resolved_edges: list[EntityEdge] = []
        invalidated: list[EntityEdge] = []
        new_edges: list[EntityEdge] = []
        seen: set[tuple[str, str, str]] = set()
        routing = {"clients": clients} if uses_prompt_routing(clients) else {}
        for edge in edges:
            key = (
                edge.source_node_uuid,
                edge.target_node_uuid,
                " ".join(edge.fact.lower().split()),
            )
            if key in seen:
                continue
            seen.add(key)
            related = _same_pair_edges(edge, existing_edges)
            related_ids = {r.uuid for r in related}
            ends = {edge.source_node_uuid, edge.target_node_uuid}
            pool = [
                e
                for e in existing_edges
                if e.uuid not in related_ids
                and (mode == "whole_group" or ends & {e.source_node_uuid, e.target_node_uuid})
            ]
            resolved, _duplicates, candidates = await _dedupe_extracted_edge(
                clients.llm_client, edge, related, pool, episode, {}, **routing
            )
            resolved_edges.append(resolved)
            if resolved.uuid == edge.uuid:
                new_edges.append(resolved)
            if candidates is not None:
                invalidated.extend(_apply_temporal_invalidation(resolved, candidates))
        return resolved_edges, invalidated, new_edges

    async def _ingest(
        self, clients: GraphitiClients, event_id: str, mode: str, always_model: bool = False
    ) -> dict[str, Any]:
        """Turn one stored episode into entities, facts and summaries, and store them.

        It takes the clients, the event id, the candidate mode and whether every entity summary
        goes through the model (Graphiti's own rule appends facts to a short summary without the
        model). It returns the entities, the facts (with their dates), the facts it ended and the
        seconds each phase took. Raises ValueError when the event has no episode, and stores
        nothing when any model call fails.
        """
        clock: dict[str, float] = {}

        def lap(name: str, since: float) -> float:
            """Record the seconds since a start and return the new time."""
            now = asyncio.get_running_loop().time()
            clock[name] = round(now - since, 2)
            return now

        loaded = await self.database.read(lambda handle: self._load(handle, event_id))
        episode, previous = loaded["episode"], loaded["previous"]
        t = asyncio.get_running_loop().time()
        extracted, index_map = await extract_nodes(clients, episode, previous, None, None, None)
        t = lap("extract_entities_seconds", t)
        nodes, uuid_map = await self._resolve_nodes(
            clients, extracted, episode, previous, loaded["nodes"]
        )
        t = lap("resolve_entities_seconds", t)
        edge_type_map = {("Entity", "Entity"): []}
        extracted_edges, materialized = await extract_edges(
            clients,
            episode,
            extracted,
            previous,
            edge_type_map,
            episode.group_id,
            None,
            False,
            None,
        )
        for node in materialized:
            nodes.append(node)
            uuid_map[node.uuid] = node.uuid
        t = lap("extract_facts_seconds", t)
        resolved, invalidated, new_edges = await self._resolve_edges(
            clients,
            resolve_edge_pointers(extracted_edges, uuid_map),
            episode,
            loaded["edges"],
            mode,
        )
        t = lap("date_and_contradiction_seconds", t)
        await _extract_entity_summaries_batch(
            clients.llm_client,
            nodes,
            episode,
            previous,
            None,
            _build_edges_by_node(new_edges),
            skip_fact_appending=always_model,
            entity_types=None,
            clients=clients,
        )
        lap("summaries_seconds", t)
        entity_edges = resolved + invalidated
        episode.entity_edges = [e.uuid for e in entity_edges]
        episodic_edges = build_episodic_edges(nodes, [episode.uuid], utc_now(), index_map)

        def save(handle: Any) -> None:
            """Store the entities, then the facts, then the links, then the episode, in one transaction."""
            driver = DuckDBDriver(connection=handle)
            for node in nodes:
                _drive(driver.entity_node_ops.save(driver, node))
            for edge in entity_edges:
                _drive(driver.entity_edge_ops.save(driver, edge))
            for link in episodic_edges:
                _drive(driver.episodic_edge_ops.save(driver, link))
            _drive(driver.episode_node_ops.save(driver, episode))

        await self.database.write(save)
        return {
            "event_id": event_id,
            "candidates": mode,
            "entities": [{"uuid": n.uuid, "name": n.name, "summary": n.summary} for n in nodes],
            "facts": [self._fact(e, e in new_edges) for e in resolved],
            "ended_facts": [self._fact(e, False) for e in invalidated],
            **clock,
        }

    @staticmethod
    def _fact(edge: EntityEdge, new: bool) -> dict[str, Any]:
        """Return a fact as plain data: its words, its four timestamps and whether it is new."""

        def iso(moment: datetime | None) -> str | None:
            """Return a moment as text, or None when there is none."""
            return None if moment is None else moment.isoformat()

        return {
            "uuid": edge.uuid,
            "fact": edge.fact,
            "source": edge.source_node_uuid,
            "target": edge.target_node_uuid,
            "created_at": iso(edge.created_at),
            "valid_at": iso(edge.valid_at),
            "invalid_at": iso(edge.invalid_at),
            "expired_at": iso(edge.expired_at),
            "new": new,
        }

    async def _communities(self, clients: GraphitiClients, group_id: str) -> dict[str, Any]:
        """Group a group's entities by their connections and have the model summarise and name each.

        Original: graphiti_core/utils/maintenance/community_operations.py, build_communities. It takes
        the clients and a group id and returns each community's name, summary and member names.
        """

        def read(handle: Any) -> list[list[EntityNode]]:
            """Read the clusters of connected entities; no model."""
            driver = DuckDBDriver(connection=handle)
            return _drive(get_community_clusters(driver, [group_id]))

        clusters = await self.database.read(read)
        built = [await build_community(clients.llm_client, c, clients=clients) for c in clusters]

        def save(handle: Any) -> None:
            """Store each community and its links in one transaction."""
            driver = DuckDBDriver(connection=handle)
            for community, links in built:
                _drive(driver.community_node_ops.save(driver, community))
                for link in links:
                    _drive(driver.community_edge_ops.save(driver, link))

        await self.database.write(save)
        return {
            "communities": [
                {
                    "name": community.name,
                    "summary": community.summary,
                    "members": [n.name for n in cluster],
                }
                for (community, _), cluster in zip(built, clusters, strict=True)
            ]
        }
