"""Graphiti MCP server on the project's DuckDB graph, exposing Graphiti through MCP tools.

Ported from Graphiti's mcp_server/src/graphiti_mcp_server.py at commit d50741d1. The thirteen
tools keep the original names, arguments and answers. Differences, all deliberate:

- The database is the project's one DuckDB file (``DuckDBDriver``); the Neo4j and FalkorDB
  branches are gone, and ``get_status`` asks the graph tables instead of running Cypher.
- Models are supplied by the owner, never built from provider settings. With none supplied,
  a tool that needs one answers with an error that says so; tools that need none still work.
- Nothing operational has a default: transport, host, port, concurrency and the default
  group come from the settings file (see ``mcp_config``).
- Graphiti's usage telemetry is switched off before the client is built.
- State lives on one ``GraphitiMcpServer`` object, not in module globals.

Goes in: a settings file and optional model objects. Comes out: a running MCP server. It never
calls ``agent_embedding``; embeddings must come from the supplied embedder.
"""

import argparse
import asyncio
import copy
import logging
import os
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.driver import GraphDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, ModelSize
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode, SagaNode
from graphiti_core.prompts.models import Message
from graphiti_core.search.search_config_recipes import (
    NODE_HYBRID_SEARCH_NODE_DISTANCE,
    NODE_HYBRID_SEARCH_RRF,
)
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.telemetry.telemetry import TELEMETRY_ENV_VAR
from graphiti_core.utils.maintenance.graph_data_operations import clear_data
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from .duckdb_driver import DuckDBDriver
from .mcp_config import TRANSPORTS, GraphitiConfig
from .mcp_queue_service import JOB_ERRORS, QueueService
from .mcp_responses import (
    BuildCommunitiesResponse,
    CommunityResult,
    EpisodeEntitiesResponse,
    EpisodeSearchResponse,
    ErrorResponse,
    FactSearchResponse,
    NodeSearchResponse,
    SagaSummaryResponse,
    StatusResponse,
    SuccessResponse,
    TripletResponse,
    format_fact_result,
    to_edge_result,
    to_node_result,
)
from .mcp_type_config import (
    build_edge_type_map,
    build_edge_types,
    build_entity_types,
    build_fact_search_filters,
    coerce_group_ids,
    parse_reference_time,
)

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

GRAPHITI_MCP_INSTRUCTIONS = """
Graphiti is a memory service for AI agents built on a temporally-aware knowledge graph. It performs
well with dynamic data such as user interactions, changing enterprise data, and external information.

Graphiti organizes data as episodes (content snippets), nodes (entities), and facts (relationships
between entities). Each piece of information is partitioned by group_id, so you can maintain separate
knowledge domains in one graph.

Bi-temporal model: every episode records both when it was ingested and when the events it describes
actually occurred. Pass reference_time to add_memory to set the event-occurrence time; otherwise the
current time is used. Facts carry valid_at / invalid_at metadata, so a fact can be superseded by newer
information while its history is preserved.

Core tools:
- add_memory: add an episode (text, message, or JSON). Supports reference_time (bi-temporal),
  excluded_entity_types and custom_extraction_instructions to steer extraction,
  previous_episode_uuids to supply explicit context, update_communities to refresh community summaries,
  and saga / saga_previous_episode_uuid to associate the episode with an ordered saga.
- add_triplet: write a single fact (source entity -> fact -> target entity) directly, bypassing
  extraction. graphiti-core resolves/deduplicates the endpoint entities and generates embeddings.
- search_nodes: semantic + keyword + graph search over entities, optionally filtered by entity type
  (node label) and re-ranked around a center_node_uuid.
- search_memory_facts: search over facts (edges), optionally filtered by edge (fact) type and by
  valid_at / invalid_at date ranges.
- summarize_saga: generate or refresh the running summary of a saga's episodes.
- build_communities: detect entity communities and produce higher-level community summaries.
- get_episode_entities: trace provenance — the entities and facts created by specific episode UUIDs.
- get_entity_edge / get_episodes: retrieve specific facts or episodes.
- delete_episode: remove an episode and cascade-delete the entities/facts it solely created.
- delete_entity_edge / clear_graph: remove a fact, or clear a group's data.

Custom types: the server can register rich entity-type and edge-type models (with attributes and
extraction instructions) from configuration, and constrain which edge types are allowed between which
entity types via an edge_type_map. With no such configuration, default extraction behavior applies.

When adding information, provide descriptive names and detailed content to improve search quality.
When searching, use specific queries and consider filtering by group_id, type, or date range. The
server needs a language model and an embedder supplied by its owner for the operations that use
them; the rest work without.
"""


class ModelNotSuppliedError(RuntimeError):
    """Raised when an operation needs a model that the owner has not supplied."""


class UnavailableLLMClient(LLMClient):
    """A language-model stand-in that refuses every request with a clear reason.

    Goes in: nothing. Comes out: only ``ModelNotSuppliedError``. It never contacts any service
    and never makes up an answer.
    """

    def __init__(self) -> None:
        """Build the stand-in with Graphiti's own empty settings."""
        super().__init__(None)

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Refuse: no language model has been supplied to this server."""
        raise ModelNotSuppliedError("no language model has been supplied to this server")


class UnavailableEmbedder(EmbedderClient):
    """An embedder stand-in that refuses every request with a clear reason.

    Goes in: nothing. Comes out: only ``ModelNotSuppliedError``. It never contacts any service
    and never returns a made-up vector.
    """

    async def create(self, input_data: Any) -> list[float]:
        """Refuse: no embedder has been supplied to this server."""
        raise ModelNotSuppliedError("no embedder has been supplied to this server")

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """Refuse: no embedder has been supplied to this server."""
        raise ModelNotSuppliedError("no embedder has been supplied to this server")


class UnavailableCrossEncoder(CrossEncoderClient):
    """A reranker stand-in that refuses every request with a clear reason.

    Goes in: nothing. Comes out: only ``ModelNotSuppliedError``. It never contacts any service
    and never returns a made-up ranking.
    """

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        """Refuse: no reranker has been supplied to this server."""
        raise ModelNotSuppliedError("no reranker has been supplied to this server")


def _driver_for_group(client: Graphiti, group_id: str) -> GraphDriver:
    """Return the driver bound to the graph that stores a group.

    Goes in: the client and the group id. Comes out: the driver. On DuckDB every group lives in
    the same graph tables, so this is the base driver unchanged.
    """
    return client.driver.clone(database=group_id)


def _client_for_group(client: Graphiti, group_id: str) -> Graphiti:
    """Return a client whose driver is bound to the group's graph.

    Goes in: the client and the group id. Comes out: the same client when the driver is
    unchanged, otherwise a copy bound to the group's driver. It never changes the original.
    """
    driver = _driver_for_group(client, group_id)
    if driver is client.driver:
        return client
    scoped = copy.copy(client)
    scoped.driver = driver
    scoped.clients = client.clients.model_copy(update={"driver": driver})
    return scoped


class GraphitiService:
    """Own the one Graphiti client, built on the DuckDB file and the owner's models."""

    def __init__(
        self,
        config: GraphitiConfig,
        llm_client: LLMClient | None = None,
        embedder: EmbedderClient | None = None,
        cross_encoder: CrossEncoderClient | None = None,
    ) -> None:
        """Remember the settings and the supplied models; build nothing yet.

        Goes in: the settings and any models the owner supplies. Comes out: the service. A
        model that is not supplied is replaced by a stand-in that refuses with a reason.
        """
        self.config = config
        self.semaphore_limit = config.server.semaphore_limit
        self.semaphore = asyncio.Semaphore(self.semaphore_limit)
        self.llm_client = llm_client
        self.embedder = embedder
        self.cross_encoder = cross_encoder
        self.client: Graphiti | None = None
        self.entity_types: dict[str, type[BaseModel]] | None = None
        self.edge_types: dict[str, type[BaseModel]] | None = None
        self.edge_type_map: dict[tuple[str, str], list[str]] | None = None

    async def initialize(self) -> None:
        """Open the DuckDB graph and build the Graphiti client and its indexes.

        Goes in: nothing. Comes out: nothing; ``client`` is set. Graphiti's usage telemetry is
        switched off first, and an unsupplied model is replaced by a refusing stand-in.
        """
        os.environ[TELEMETRY_ENV_VAR] = "false"
        self.entity_types = build_entity_types(self.config.graphiti.entity_types)
        self.edge_types = build_edge_types(self.config.graphiti.edge_types)
        self.edge_type_map = build_edge_type_map(self.config.graphiti.edge_type_map)
        driver = DuckDBDriver(db=self.config.database.path)
        self.client = Graphiti(
            graph_driver=driver,
            llm_client=self.llm_client or UnavailableLLMClient(),
            embedder=self.embedder or UnavailableEmbedder(),
            cross_encoder=self.cross_encoder or UnavailableCrossEncoder(),
            max_coroutines=self.semaphore_limit,
        )
        await self.client.build_indices_and_constraints()
        logger.info("Successfully initialized Graphiti client")
        logger.info(
            "Language model: %s",
            "supplied" if self.llm_client else "not supplied (extraction tools will refuse)",
        )
        logger.info(
            "Embedder: %s",
            "supplied" if self.embedder else "not supplied (search and writes of text will refuse)",
        )
        logger.info("Using database file: %s", self.config.database.path)
        logger.info("Using group_id: %s", self.config.graphiti.group_id)

    async def get_client(self) -> Graphiti:
        """Return the Graphiti client, building it on first use."""
        if self.client is None:
            await self.initialize()
        if self.client is None:
            raise RuntimeError("Failed to initialize Graphiti client")
        return self.client

    async def close(self) -> None:
        """Close the DuckDB connection this service opened; does nothing if never built."""
        if self.client is not None:
            await self.client.driver.close()
            self.client = None


class GraphitiMcpServer:
    """The MCP server: thirteen tools over one Graphiti service and one episode queue."""

    def __init__(self, config: GraphitiConfig, service: GraphitiService) -> None:
        """Create the MCP server object and register every tool on it.

        Goes in: the settings and the service. Comes out: the server, not yet started. It
        never opens the database until a tool or ``start`` needs it.
        """
        self.config = config
        self.service = service
        self.queue = QueueService()
        self._queue_ready = False
        self.mcp = MCPServer("Graphiti Agent Memory", instructions=GRAPHITI_MCP_INSTRUCTIONS)
        for tool in (
            self.add_memory,
            self.search_nodes,
            self.search_memory_facts,
            self.delete_entity_edge,
            self.delete_episode,
            self.get_entity_edge,
            self.get_episodes,
            self.summarize_saga,
            self.build_communities,
            self.add_triplet,
            self.get_episode_entities,
            self.clear_graph,
            self.get_status,
        ):
            self.mcp.add_tool(tool)
        self.mcp.custom_route("/health", methods=["GET"])(self.health_check)

    async def start(self) -> None:
        """Build the Graphiti client and hand it to the episode queue."""
        client = await self.service.get_client()
        if not self._queue_ready:
            await self.queue.initialize(client)
            self._queue_ready = True

    async def stop(self) -> None:
        """Stop the queue workers and close the database."""
        await self.queue.stop()
        await self.service.close()

    def _effective_group_ids(self, group_ids: str | list[str] | None) -> list[str]:
        """Pick the groups a call acts on: the ones named, else the configured default.

        Goes in: a group id, a list or None. Comes out: a list, empty when none was named and
        no default is configured.
        """
        named = coerce_group_ids(group_ids)
        if named is not None:
            return named
        default = self.config.graphiti.group_id
        return [default] if default else []

    async def add_memory(
        self,
        name: str,
        episode_body: str,
        group_id: str | None = None,
        source: str = "text",
        source_description: str = "",
        uuid: str | None = None,
        reference_time: str | None = None,
        excluded_entity_types: list[str] | None = None,
        custom_extraction_instructions: str | None = None,
        previous_episode_uuids: list[str] | None = None,
        update_communities: bool = False,
        saga: str | None = None,
        saga_previous_episode_uuid: str | None = None,
    ) -> SuccessResponse | ErrorResponse:
        """Add an episode to memory. This is the primary way to add information to the graph.

        This function returns immediately and processes the episode addition in the background.
        Episodes for the same group_id are processed sequentially to avoid race conditions.

        Graphiti uses a bi-temporal model: each episode records both when it was ingested and
        when the described events actually occurred (its reference_time). Pass reference_time to
        set the event-occurrence time explicitly; otherwise the current time is used.

        Args:
            name (str): Name of the episode
            episode_body (str): The content of the episode to persist to memory. When source='json', this must be a
                               properly escaped JSON string, not a raw Python dictionary. The JSON data will be
                               automatically processed to extract entities and relationships.
            group_id (str, optional): A unique ID for this graph. If not provided, uses the default group_id from
                                     the settings.
            source (str, optional): Source type, must be one of:
                                   - 'text': For plain text content (default)
                                   - 'json': For structured data
                                   - 'message': For conversation-style content
            source_description (str, optional): Description of the source
            uuid (str, optional): Optional UUID for the episode
            reference_time (str, optional): ISO-8601 timestamp for when the described events occurred
                                     (e.g. "2025-01-15T10:30:00Z" or "2025-01-15T10:30:00+00:00"). A
                                     timezone-naive value is interpreted as UTC. Defaults to the
                                     current time when omitted.
            excluded_entity_types (list[str], optional): Names of configured entity types to exclude
                                     from extraction for this episode.
            custom_extraction_instructions (str, optional): Additional natural-language guidance passed
                                     to the extraction model for this episode only.
            previous_episode_uuids (list[str], optional): Explicit list of prior episode UUIDs to use
                                     as conversational/contextual history. Overrides automatic retrieval.
            update_communities (bool, optional): When True, incrementally update community summaries to
                                     reflect entities affected by this episode. Defaults to False.
            saga (str, optional): Name/id of a saga to associate this episode with. Sagas group related
                                     episodes so their evolving narrative can be summarized via summarize_saga.
            saga_previous_episode_uuid (str, optional): UUID of the preceding episode in the saga, used to
                                     order episodes within the saga.

        Examples:
            add_memory(
                name="Company News",
                episode_body="Acme Corp announced a new product line today.",
                source="text",
                source_description="news article",
                group_id="some_arbitrary_string"
            )

            add_memory(
                name="Historical Note",
                episode_body="The merger closed in early 2020.",
                reference_time="2020-03-01T00:00:00Z"
            )
        """
        try:
            parsed_reference_time = parse_reference_time(reference_time)
        except ValueError as error:
            return ErrorResponse(error=f"Invalid reference_time: {error}")

        try:
            await self.start()
            effective_group_id = group_id or self.config.graphiti.group_id
            episode_type = EpisodeType.text
            if source:
                try:
                    episode_type = EpisodeType[source.lower()]
                except (KeyError, AttributeError):
                    logger.warning("Unknown source type '%s', using 'text' as default", source)
            await self.queue.add_episode(
                group_id=effective_group_id,
                name=name,
                content=episode_body,
                source_description=source_description,
                episode_type=episode_type,
                entity_types=self.service.entity_types,
                uuid=uuid or None,
                reference_time=parsed_reference_time,
                edge_types=self.service.edge_types,
                edge_type_map=self.service.edge_type_map,
                excluded_entity_types=excluded_entity_types,
                previous_episode_uuids=previous_episode_uuids,
                custom_extraction_instructions=custom_extraction_instructions,
                update_communities=update_communities,
                saga=saga,
                saga_previous_episode_uuid=saga_previous_episode_uuid,
            )
            return SuccessResponse(
                message=f"Episode '{name}' queued for processing in group '{effective_group_id}'"
            )
        except JOB_ERRORS as error:
            logger.error("Error queuing episode: %s", error)
            return ErrorResponse(error=f"Error queuing episode: {error}")

    async def search_nodes(
        self,
        query: str,
        group_ids: str | list[str] | None = None,
        max_nodes: int = 10,
        entity_types: list[str] | None = None,
        center_node_uuid: str | None = None,
    ) -> NodeSearchResponse | ErrorResponse:
        """Search for nodes (entities) in the graph memory.

        Args:
            query: The search query
            group_ids: Optional group ID, or list of group IDs, to filter results (a single
                string is accepted and treated as a one-element list)
            max_nodes: Maximum number of nodes to return (default: 10)
            entity_types: Optional list of entity type names (node labels) to filter by
            center_node_uuid: Optional UUID of a node to center the search around. Results
                closer to this node in the graph are ranked higher.
        """
        try:
            client = await self.service.get_client()
            results = await client.search_(
                query=query,
                config=(
                    NODE_HYBRID_SEARCH_NODE_DISTANCE if center_node_uuid else NODE_HYBRID_SEARCH_RRF
                ),
                group_ids=self._effective_group_ids(group_ids),
                center_node_uuid=center_node_uuid,
                search_filter=SearchFilters(node_labels=entity_types),
            )
            nodes = results.nodes[:max_nodes] if results.nodes else []
            if not nodes:
                return NodeSearchResponse(message="No relevant nodes found", nodes=[])
            return NodeSearchResponse(
                message="Nodes retrieved successfully",
                nodes=[to_node_result(node) for node in nodes],
            )
        except JOB_ERRORS as error:
            logger.error("Error searching nodes: %s", error)
            return ErrorResponse(error=f"Error searching nodes: {error}")

    async def search_memory_facts(
        self,
        query: str,
        group_ids: str | list[str] | None = None,
        max_facts: int = 10,
        center_node_uuid: str | None = None,
        edge_types: list[str] | None = None,
        valid_at_after: str | None = None,
        valid_at_before: str | None = None,
        invalid_at_after: str | None = None,
        invalid_at_before: str | None = None,
    ) -> FactSearchResponse | ErrorResponse:
        """Search the graph memory for relevant facts (entity edges).

        Args:
            query: The search query
            group_ids: Optional group ID, or list of group IDs, to filter results (a single
                string is accepted and treated as a one-element list)
            max_facts: Maximum number of facts to return (default: 10)
            center_node_uuid: Optional UUID of a node to center the search around
            edge_types: Optional list of edge (fact) type names to filter by
            valid_at_after: Optional ISO-8601 lower bound; only facts whose valid_at is at or
                after this time are returned (timezone-naive is treated as UTC)
            valid_at_before: Optional ISO-8601 upper bound on a fact's valid_at
            invalid_at_after: Optional ISO-8601 lower bound on a fact's invalid_at
            invalid_at_before: Optional ISO-8601 upper bound on a fact's invalid_at
        """
        try:
            if max_facts <= 0:
                return ErrorResponse(error="max_facts must be a positive integer")
            try:
                search_filter = build_fact_search_filters(
                    edge_types=edge_types,
                    valid_at_after=valid_at_after,
                    valid_at_before=valid_at_before,
                    invalid_at_after=invalid_at_after,
                    invalid_at_before=invalid_at_before,
                )
            except ValueError as error:
                return ErrorResponse(error=f"Invalid date filter: {error}")
            client = await self.service.get_client()
            relevant_edges = await client.search(
                group_ids=self._effective_group_ids(group_ids),
                query=query,
                num_results=max_facts,
                center_node_uuid=center_node_uuid,
                search_filter=search_filter,
            )
            if not relevant_edges:
                return FactSearchResponse(message="No relevant facts found", facts=[])
            return FactSearchResponse(
                message="Facts retrieved successfully",
                facts=[format_fact_result(edge) for edge in relevant_edges],
            )
        except JOB_ERRORS as error:
            logger.error("Error searching facts: %s", error)
            return ErrorResponse(error=f"Error searching facts: {error}")

    async def delete_entity_edge(
        self, uuid: str, group_id: str | None = None
    ) -> SuccessResponse | ErrorResponse:
        """Delete an entity edge from the graph memory.

        Args:
            uuid: UUID of the entity edge to delete
            group_id: Optional group ID. Falls back to the default group when omitted.
        """
        try:
            client = await self.service.get_client()
            effective_group_id = group_id or self.config.graphiti.group_id
            driver = (
                _driver_for_group(client, effective_group_id)
                if effective_group_id
                else client.driver
            )
            entity_edge = await EntityEdge.get_by_uuid(driver, uuid)
            await entity_edge.delete(driver)
            return SuccessResponse(message=f"Entity edge with UUID {uuid} deleted successfully")
        except JOB_ERRORS as error:
            logger.error("Error deleting entity edge: %s", error)
            return ErrorResponse(error=f"Error deleting entity edge: {error}")

    async def delete_episode(
        self, uuid: str, group_id: str | None = None
    ) -> SuccessResponse | ErrorResponse:
        """Delete an episode from the graph memory.

        Uses Graphiti.remove_episode, which cascades the deletion: entities and facts
        that were created solely by this episode are removed along with it, while
        entities and facts also supported by other episodes are preserved.

        Args:
            uuid: UUID of the episode to delete
            group_id: Optional group ID. Falls back to the default group when omitted.
        """
        try:
            client = await self.service.get_client()
            effective_group_id = group_id or self.config.graphiti.group_id
            scoped_client = (
                _client_for_group(client, effective_group_id) if effective_group_id else client
            )
            await scoped_client.remove_episode(uuid)
            return SuccessResponse(message=f"Episode with UUID {uuid} deleted successfully")
        except JOB_ERRORS as error:
            logger.error("Error deleting episode: %s", error)
            return ErrorResponse(error=f"Error deleting episode: {error}")

    async def get_entity_edge(
        self, uuid: str, group_id: str | None = None
    ) -> dict[str, Any] | ErrorResponse:
        """Get an entity edge from the graph memory by its UUID.

        Args:
            uuid: UUID of the entity edge to retrieve
            group_id: Optional group ID. Falls back to the default group when omitted.
        """
        try:
            client = await self.service.get_client()
            effective_group_id = group_id or self.config.graphiti.group_id
            driver = (
                _driver_for_group(client, effective_group_id)
                if effective_group_id
                else client.driver
            )
            entity_edge = await EntityEdge.get_by_uuid(driver, uuid)
            return format_fact_result(entity_edge)
        except JOB_ERRORS as error:
            logger.error("Error getting entity edge: %s", error)
            return ErrorResponse(error=f"Error getting entity edge: {error}")

    async def get_episodes(
        self,
        group_ids: str | list[str] | None = None,
        max_episodes: int = 10,
    ) -> EpisodeSearchResponse | ErrorResponse:
        """Get episodes from the graph memory.

        Args:
            group_ids: Optional group ID, or list of group IDs, to filter results (a single
                string is accepted and treated as a one-element list)
            max_episodes: Maximum number of episodes to return (default: 10)
        """
        try:
            client = await self.service.get_client()
            effective_group_ids = self._effective_group_ids(group_ids)
            episodes: list[EpisodicNode] = []
            for group_id in effective_group_ids:
                episodes.extend(
                    await EpisodicNode.get_by_group_ids(
                        _driver_for_group(client, group_id), [group_id], limit=max_episodes
                    )
                )
            episodes.sort(
                key=lambda episode: episode.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            episodes = episodes[:max_episodes]
            if not episodes:
                return EpisodeSearchResponse(message="No episodes found", episodes=[])
            episode_results = [
                {
                    "uuid": episode.uuid,
                    "name": episode.name,
                    "content": episode.content,
                    "created_at": episode.created_at.isoformat() if episode.created_at else None,
                    "source": episode.source.value
                    if hasattr(episode.source, "value")
                    else str(episode.source),
                    "source_description": episode.source_description,
                    "group_id": episode.group_id,
                }
                for episode in episodes
            ]
            return EpisodeSearchResponse(
                message="Episodes retrieved successfully", episodes=episode_results
            )
        except JOB_ERRORS as error:
            logger.error("Error getting episodes: %s", error)
            return ErrorResponse(error=f"Error getting episodes: {error}")

    async def summarize_saga(
        self, saga_name: str, group_id: str | None = None
    ) -> SagaSummaryResponse | ErrorResponse:
        """Summarize a saga: an ordered group of related episodes.

        Generates (or refreshes) a running summary of the saga's narrative across its
        episodes and returns the saga's name and summary text.

        Sagas are keyed by (name, group_id): pass the same ``saga`` name you used with
        add_memory. This tool resolves that name to the saga within the group and
        summarizes it.

        Args:
            saga_name: The saga name — the same value passed as ``saga`` to add_memory.
            group_id: Optional group ID the saga belongs to. Falls back to the default group.
        """
        try:
            client = await self.service.get_client()
            effective_group_id = group_id or self.config.graphiti.group_id
            if not effective_group_id:
                return ErrorResponse(
                    error="No group_id provided and no default group_id is configured"
                )
            sagas = await SagaNode.get_by_group_ids(
                _driver_for_group(client, effective_group_id), [effective_group_id]
            )
            match = next((saga for saga in sagas if saga.name == saga_name), None)
            if match is None:
                return ErrorResponse(
                    error=f"No saga named '{saga_name}' found in group '{effective_group_id}'"
                )
            saga_node = await _client_for_group(client, effective_group_id).summarize_saga(
                match.uuid
            )
            return SagaSummaryResponse(
                message=f"Saga '{saga_name}' summarized successfully",
                uuid=saga_node.uuid,
                name=saga_node.name,
                summary=saga_node.summary,
            )
        except JOB_ERRORS as error:
            logger.error("Error summarizing saga: %s", error)
            return ErrorResponse(error=f"Error summarizing saga: {error}")

    async def build_communities(
        self,
        group_ids: str | list[str] | None = None,
    ) -> BuildCommunitiesResponse | ErrorResponse:
        """Detect and build community summaries over the graph's entities.

        Communities group densely-connected entities and produce higher-level summaries
        that can then be searched. This is a relatively expensive operation that
        processes the full set of entities for the given group(s).

        Args:
            group_ids: Optional group ID, or list of group IDs, to build communities for.
                Falls back to the default group when omitted. Pass an explicit list to scope
                community detection across multiple graphs.
        """
        try:
            client = await self.service.get_client()
            normalized_group_ids = coerce_group_ids(group_ids)
            if normalized_group_ids is None and self.config.graphiti.group_id:
                normalized_group_ids = [self.config.graphiti.group_id]
            communities, community_edges = await client.build_communities(
                group_ids=normalized_group_ids
            )
            community_results: list[CommunityResult] = [
                CommunityResult(
                    uuid=community.uuid,
                    name=community.name,
                    group_id=community.group_id,
                    summary=getattr(community, "summary", None),
                )
                for community in communities
            ]
            return BuildCommunitiesResponse(
                message=f"Built {len(communities)} communities",
                community_count=len(communities),
                edge_count=len(community_edges),
                communities=community_results,
            )
        except JOB_ERRORS as error:
            logger.error("Error building communities: %s", error)
            return ErrorResponse(error=f"Error building communities: {error}")

    async def add_triplet(
        self,
        source_node_name: str,
        edge_name: str,
        fact: str,
        target_node_name: str,
        group_id: str | None = None,
        source_node_uuid: str | None = None,
        target_node_uuid: str | None = None,
    ) -> TripletResponse | ErrorResponse:
        """Directly add a single fact triplet (source entity -> fact -> target entity).

        Unlike add_memory, this bypasses episode extraction and writes the relationship
        directly. graphiti-core resolves the source/target nodes against existing entities
        (deduplicating by name) and generates the required embeddings internally.

        Args:
            source_node_name: Name of the source entity
            edge_name: Relationship/edge type name (e.g. "WORKS_FOR")
            fact: Natural-language statement describing the relationship
            target_node_name: Name of the target entity
            group_id: Optional group ID. Falls back to the default group when omitted.
            source_node_uuid: Optional UUID to reuse an existing source entity. A new UUID is
                generated when omitted.
            target_node_uuid: Optional UUID to reuse an existing target entity. A new UUID is
                generated when omitted.
        """
        try:
            client = await self.service.get_client()
            effective_group_id = group_id or self.config.graphiti.group_id
            if not effective_group_id:
                return ErrorResponse(
                    error="No group_id provided and no default group_id is configured"
                )
            now = datetime.now(timezone.utc)
            source_node = EntityNode(
                uuid=source_node_uuid or str(uuid4()),
                name=source_node_name,
                group_id=effective_group_id,
                created_at=now,
            )
            target_node = EntityNode(
                uuid=target_node_uuid or str(uuid4()),
                name=target_node_name,
                group_id=effective_group_id,
                created_at=now,
            )
            edge = EntityEdge(
                name=edge_name,
                fact=fact,
                group_id=effective_group_id,
                source_node_uuid=source_node.uuid,
                target_node_uuid=target_node.uuid,
                created_at=now,
            )
            result = await _client_for_group(client, effective_group_id).add_triplet(
                source_node, edge, target_node
            )
            return TripletResponse(
                message=f"Triplet '{source_node_name} -[{edge_name}]-> {target_node_name}' added",
                nodes=[to_node_result(node) for node in result.nodes],
                edges=[to_edge_result(item) for item in result.edges],
            )
        except JOB_ERRORS as error:
            logger.error("Error adding triplet: %s", error)
            return ErrorResponse(error=f"Error adding triplet: {error}")

    async def get_episode_entities(
        self,
        episode_uuids: list[str],
        group_id: str | None = None,
    ) -> EpisodeEntitiesResponse | ErrorResponse:
        """Get the entities (nodes) and facts (edges) created by specific episodes.

        Use this to trace provenance: given one or more episode UUIDs, return the graph
        elements that those episodes produced.

        Args:
            episode_uuids: List of episode UUIDs to look up provenance for
            group_id: Optional group ID. Falls back to the default group when omitted.
        """
        if not episode_uuids:
            return ErrorResponse(error="episode_uuids must contain at least one UUID")
        try:
            client = await self.service.get_client()
            effective_group_id = group_id or self.config.graphiti.group_id
            scoped_client = (
                _client_for_group(client, effective_group_id) if effective_group_id else client
            )
            results = await scoped_client.get_nodes_and_edges_by_episode(episode_uuids)
            return EpisodeEntitiesResponse(
                message=f"Retrieved provenance for {len(episode_uuids)} episode(s)",
                nodes=[to_node_result(node) for node in results.nodes],
                edges=[to_edge_result(item) for item in results.edges],
            )
        except JOB_ERRORS as error:
            logger.error("Error getting episode entities: %s", error)
            return ErrorResponse(error=f"Error getting episode entities: {error}")

    async def clear_graph(
        self,
        group_ids: str | list[str] | None = None,
    ) -> SuccessResponse | ErrorResponse:
        """Clear all data from the graph for specified group IDs.

        Args:
            group_ids: Optional group ID, or list of group IDs, to clear (a single string is
                accepted). If not provided, clears the default group.
        """
        try:
            client = await self.service.get_client()
            effective_group_ids = self._effective_group_ids(group_ids)
            if not effective_group_ids:
                return ErrorResponse(error="No group IDs specified for clearing")
            for group_id in effective_group_ids:
                await clear_data(_driver_for_group(client, group_id), group_ids=[group_id])
            return SuccessResponse(
                message=f"Graph data cleared successfully for group IDs: {', '.join(effective_group_ids)}"
            )
        except JOB_ERRORS as error:
            logger.error("Error clearing graph: %s", error)
            return ErrorResponse(error=f"Error clearing graph: {error}")

    async def get_status(self) -> StatusResponse:
        """Get the status of the Graphiti MCP server and database connection."""
        try:
            client = await self.service.get_client()
            async with client.driver.session() as session:
                await session.run("SELECT count(*) AS count FROM graphiti.graph_node")
            return StatusResponse(
                status="ok",
                message=(
                    "Graphiti MCP server is running and connected to the DuckDB database "
                    f"{self.config.database.path}"
                ),
            )
        except JOB_ERRORS as error:
            logger.error("Error checking database connection: %s", error)
            return StatusResponse(
                status="error",
                message=f"Graphiti MCP server is running but database connection failed: {error}",
            )

    async def health_check(self, request: Request) -> JSONResponse:
        """Answer the health endpoint used by load balancers; it never touches the database."""
        return JSONResponse({"status": "healthy", "service": "graphiti-mcp"})

    async def run(self) -> None:
        """Start the server and serve on the configured transport until it stops.

        Goes in: nothing. Comes out: nothing, when the server stops. It refuses a transport
        the settings do not allow, and always stops the queue and closes the database.
        """
        await self.start()
        server = self.config.server
        logger.info("Starting MCP server with transport: %s", server.transport)
        try:
            if server.transport == "stdio":
                await self.mcp.run_stdio_async()
            elif server.transport == "sse":
                logger.info("SSE transport on %s:%s", server.host, server.port)
                await self.mcp.run_sse_async(host=server.host, port=server.port)
            elif server.transport == "http":
                logger.info("Streamable HTTP on %s:%s, endpoint /mcp", server.host, server.port)
                await self.mcp.run_streamable_http_async(host=server.host, port=server.port)
            else:
                raise ValueError(
                    f"Unsupported transport: {server.transport}. Use one of {', '.join(TRANSPORTS)}"
                )
        finally:
            await self.stop()


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser: one required settings file and optional overrides.

    Goes in: nothing. Comes out: the parser. It never supplies a default for any value.
    """
    parser = argparse.ArgumentParser(description="Run the Graphiti MCP server on DuckDB")
    parser.add_argument("--config", type=Path, required=True, help="Path to the YAML settings file")
    parser.add_argument("--transport", choices=TRANSPORTS, help="Transport to use")
    parser.add_argument("--host", help="Host to bind the MCP server to")
    parser.add_argument("--port", type=int, help="Port to bind the MCP server to")
    parser.add_argument("--semaphore-limit", type=int, help="Episodes processed at the same time")
    parser.add_argument("--database-path", help="Path of the DuckDB file")
    parser.add_argument("--group-id", help="Namespace for the graph")
    parser.add_argument("--user-id", help="User ID for tracking operations")
    parser.add_argument(
        "--destroy-graph", action="store_true", help="Destroy all Graphiti data on startup"
    )
    return parser


async def run_mcp_server(arguments: Iterable[str] | None = None) -> None:
    """Read the settings, optionally clear the graph, then run the server.

    Goes in: the command-line arguments, or None to read them from the process. Comes out:
    nothing, when the server stops. Models are not supplied from here; an owner that has them
    builds ``GraphitiService`` itself and passes them in.
    """
    args = build_argument_parser().parse_args(None if arguments is None else list(arguments))
    config = GraphitiConfig.from_yaml(args.config)
    config.apply_cli_overrides(args)
    logger.info("Using configuration: %s", config.model_dump_json())
    service = GraphitiService(config)
    if args.destroy_graph:
        logger.warning("Destroying all Graphiti graphs as requested...")
        client = await service.get_client()
        await clear_data(client.driver)
        logger.info("All graphs destroyed")
    await GraphitiMcpServer(config, service).run()


def main() -> None:
    """Run the Graphiti MCP server from the command line."""
    logging.basicConfig(
        level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT, stream=sys.stderr
    )
    try:
        asyncio.run(run_mcp_server())
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    except JOB_ERRORS as error:
        logger.error("Error initializing Graphiti MCP server: %s", error)
        raise


if __name__ == "__main__":
    main()
