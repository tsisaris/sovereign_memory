"""Bind Graphiti's community, saga and episode-link operation contracts to native storage.

Communities, sagas and their HAS_MEMBER, HAS_EPISODE and NEXT_EPISODE edges live in the
same ``graphiti`` tables as entities and episodes. Saga reads mirror the maintained
Cypher fallbacks in ``graphiti.py``. Nothing here calls a model or returns invented data.
"""

from graphiti_core.driver.operations.community_edge_ops import CommunityEdgeOperations
from graphiti_core.driver.operations.community_node_ops import CommunityNodeOperations
from graphiti_core.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
from graphiti_core.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
from graphiti_core.driver.operations.saga_node_ops import SagaNodeOperations
from graphiti_core.edges import CommunityEdge, HasEpisodeEdge, NextEpisodeEdge
from graphiti_core.nodes import CommunityNode, EpisodicNode, SagaNode

from .families import EdgeFamilyOps, NodeFamilyOps, read_models

_EPISODE_TIME = "CAST(json_extract_string(ep.properties, '{}') AS TIMESTAMPTZ)"


class DuckDBCommunityNodeOps(NodeFamilyOps, CommunityNodeOperations):
    """Store community summaries and their name embeddings under the Community label."""

    label = "Community"
    model = CommunityNode

    async def load_name_embedding(self, executor, node):
        """Copy the stored name embedding onto ``node``; raise if the community is missing."""
        node.name_embedding = (await self.get_by_uuid(executor, node.uuid)).name_embedding


class DuckDBSagaNodeOps(NodeFamilyOps, SagaNodeOperations):
    """Store sagas and answer the saga questions Graphiti's episode flow asks."""

    label = "Saga"
    model = SagaNode

    async def get_by_name(self, executor, name, group_id):
        """Return the saga with this exact name in this group, or None when none exists.

        When concurrent writers created duplicates, the lowest UUID is chosen so repeated
        calls agree. Never creates a saga.
        """
        sagas = await read_models(
            executor,
            "graph_node",
            self.label,
            self.model,
            "json_extract_string(properties, 'name') = ? "
            "AND json_extract_string(properties, 'group_id') = ?",
            [name, group_id],
        )
        return min(sagas, key=lambda saga: saga.uuid) if sagas else None

    async def get_previous_episode_uuid(self, executor, saga_uuid, current_episode_uuid):
        """Return the latest other episode in the saga by valid_at then created_at, or None."""
        records, _, _ = await executor.execute_query(
            "SELECT ep.uuid FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node ep ON ep.uuid = e.target_node_uuid "
            "WHERE e.label = 'HAS_EPISODE' AND e.source_node_uuid = ? "
            "AND ep.label = 'Episodic' AND ep.uuid <> ? "
            f"ORDER BY {_EPISODE_TIME.format('valid_at')} DESC NULLS LAST, "
            f"{_EPISODE_TIME.format('created_at')} DESC NULLS LAST, ep.uuid DESC LIMIT 1",
            parameters=[saga_uuid, current_episode_uuid],
        )
        return records[0]["uuid"] if records else None

    async def get_episode_contents(self, executor, saga_uuid, since=None, limit=200):
        """Return (content, valid_at, created_at) for saga episodes created after ``since``.

        Order and filtering follow the maintained fallback: oldest created_at first, only
        episodes with content and a creation time, at most ``limit`` rows.
        """
        query = (
            "SELECT ep.properties FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node ep ON ep.uuid = e.target_node_uuid "
            "WHERE e.label = 'HAS_EPISODE' AND e.source_node_uuid = ? AND ep.label = 'Episodic' "
            f"AND {_EPISODE_TIME.format('created_at')} IS NOT NULL"
        )
        parameters = [saga_uuid]
        if since is not None:
            query += f" AND {_EPISODE_TIME.format('created_at')} > ?"
            parameters.append(since)
        query += (
            f" ORDER BY {_EPISODE_TIME.format('created_at')} ASC, "
            f"{_EPISODE_TIME.format('valid_at')} ASC, ep.uuid ASC LIMIT ?"
        )
        parameters.append(limit)
        records, _, _ = await executor.execute_query(query, parameters=parameters)
        episodes = [EpisodicNode.model_validate_json(record["properties"]) for record in records]
        return [
            (episode.content, episode.valid_at, episode.created_at)
            for episode in episodes
            if episode.content
        ]


class DuckDBCommunityEdgeOps(EdgeFamilyOps, CommunityEdgeOperations):
    """Store HAS_MEMBER edges from a community to an entity or a nested community."""

    label = "HAS_MEMBER"
    model = CommunityEdge
    source_labels = ("Community",)
    target_labels = ("Entity", "Community")


class DuckDBHasEpisodeEdgeOps(EdgeFamilyOps, HasEpisodeEdgeOperations):
    """Store HAS_EPISODE edges from a saga to one of its episodes."""

    label = "HAS_EPISODE"
    model = HasEpisodeEdge
    source_labels = ("Saga",)
    target_labels = ("Episodic",)


class DuckDBNextEpisodeEdgeOps(EdgeFamilyOps, NextEpisodeEdgeOperations):
    """Store NEXT_EPISODE edges that order one episode after another."""

    label = "NEXT_EPISODE"
    model = NextEpisodeEdge
    source_labels = ("Episodic",)
    target_labels = ("Episodic",)
