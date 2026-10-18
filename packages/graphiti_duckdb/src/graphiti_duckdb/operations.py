"""Collect DuckDB operation objects; remaining unsupported families need full integration."""

from datetime import datetime

from graphiti_core.driver.operations.community_edge_ops import CommunityEdgeOperations
from graphiti_core.driver.operations.community_node_ops import CommunityNodeOperations
from graphiti_core.driver.operations.graph_ops import GraphMaintenanceOperations
from graphiti_core.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
from graphiti_core.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
from graphiti_core.driver.operations.saga_node_ops import SagaNodeOperations
from graphiti_core.driver.operations.search_ops import SearchOperations
from graphiti_core.nodes import CommunityNode, SagaNode

from .entity_edge_ops import DuckDBEntityEdgeOps as DuckDBEntityEdgeOps
from .entity_node_ops import DuckDBEntityNodeOps as DuckDBEntityNodeOps
from .episode_node_ops import DuckDBEpisodeNodeOps as DuckDBEpisodeNodeOps
from .episodic_edge_ops import DuckDBEpisodicEdgeOps as DuckDBEpisodicEdgeOps


class DuckDBCommunityNodeOps(CommunityNodeOperations):
    async def save(self, executor, node, tx=None):
        pass

    async def load_name_embedding(self, executor, node):
        pass

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        pass

    async def delete(self, executor, node, tx=None):
        pass

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        pass

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100):
        pass

    async def get_by_uuid(self, executor, uuid):
        return CommunityNode(name="a", group_id="g", created_at=datetime.now())

    async def get_by_uuids(self, executor, uuids):
        return []

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return []


class DuckDBSagaNodeOps(SagaNodeOperations):
    async def save(self, executor, node, tx=None):
        pass

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        pass

    async def delete(self, executor, node, tx=None):
        pass

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        pass

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100):
        pass

    async def get_by_uuid(self, executor, uuid):
        return SagaNode(name="a", group_id="g", created_at=datetime.now())

    async def get_by_uuids(self, executor, uuids):
        return []

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return []

    async def get_or_create_saga(self, executor, saga_name, group_id, created_at):
        return SagaNode(name=saga_name, group_id=group_id, created_at=created_at)

    async def saga_get_previous_episode_uuid(self, executor, saga_uuid, current_episode_uuid):
        return None

    async def summarize_saga_episodes(self, executor, saga_id, since, limit):
        return []


class DuckDBCommunityEdgeOps(CommunityEdgeOperations):
    async def save(self, executor, edge, tx=None):
        pass

    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        pass

    async def delete(self, executor, edge, tx=None):
        pass

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        pass

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100):
        pass

    async def get_by_uuid(self, executor, uuid):
        pass

    async def get_by_uuids(self, executor, uuids):
        return []

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return []


class DuckDBHasEpisodeEdgeOps(HasEpisodeEdgeOperations):
    async def save(self, executor, edge, tx=None):
        pass

    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        pass

    async def delete(self, executor, edge, tx=None):
        pass

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        pass

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100):
        pass

    async def get_by_uuid(self, executor, uuid):
        pass

    async def get_by_uuids(self, executor, uuids):
        return []

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return []


class DuckDBNextEpisodeEdgeOps(NextEpisodeEdgeOperations):
    async def save(self, executor, edge, tx=None):
        pass

    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        pass

    async def delete(self, executor, edge, tx=None):
        pass

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        pass

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100):
        pass

    async def get_by_uuid(self, executor, uuid):
        pass

    async def get_by_uuids(self, executor, uuids):
        return []

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return []


class DuckDBSearchOps(SearchOperations):
    async def node_fulltext_search(self, executor, query, search_filter, group_ids=None, limit=10):
        return []

    async def node_similarity_search(
        self, executor, search_vector, search_filter, group_ids=None, limit=10, min_score=0.6
    ):
        return []

    async def node_bfs_search(
        self, executor, origin_uuids, search_filter, max_depth, group_ids=None, limit=10
    ):
        return []

    async def edge_fulltext_search(self, executor, query, search_filter, group_ids=None, limit=10):
        return []

    async def edge_similarity_search(
        self,
        executor,
        search_vector,
        source_node_uuid,
        target_node_uuid,
        search_filter,
        group_ids=None,
        limit=10,
        min_score=0.6,
    ):
        return []

    async def edge_bfs_search(
        self, executor, origin_uuids, max_depth, search_filter, group_ids=None, limit=10
    ):
        return []

    async def episode_fulltext_search(
        self, executor, query, search_filter, group_ids=None, limit=10
    ):
        return []

    async def community_fulltext_search(self, executor, query, group_ids=None, limit=10):
        return []

    async def community_similarity_search(
        self, executor, search_vector, group_ids=None, limit=10, min_score=0.6
    ):
        return []

    async def node_distance_reranker(self, executor, node_uuids, center_node_uuid, min_score=0):
        return []

    async def episode_mentions_reranker(self, executor, node_uuids, min_score=0):
        return []

    def build_node_search_filters(self, search_filters):
        return ""

    def build_edge_search_filters(self, search_filters):
        return ""

    def build_fulltext_query(self, query, group_ids=None, max_query_length=8000):
        return ""

    async def get_relevant_nodes(self, executor, nodes, search_filter, group_id, min_score, limit):
        return []

    async def get_relevant_edges(self, executor, edges, search_filter, min_score, limit):
        return []

    async def get_edge_invalidation_candidates(
        self, executor, edges, search_filter, min_score, limit
    ):
        return []


class DuckDBGraphOps(GraphMaintenanceOperations):
    async def clear_data(self, executor, group_ids=None):
        """Delete selected Graphiti groups and incident edges without touching other engines."""
        async with executor.transaction() as transaction:
            if group_ids is None:
                await transaction.run("DELETE FROM graphiti.graph_edge")
                await transaction.run("DELETE FROM graphiti.graph_node")
            else:
                selected = (
                    "SELECT uuid FROM graphiti.graph_node "
                    "WHERE json_extract_string(properties, 'group_id') IN (SELECT unnest(?))"
                )
                await transaction.run(
                    "DELETE FROM graphiti.graph_edge "
                    "WHERE json_extract_string(properties, 'group_id') IN (SELECT unnest(?)) "
                    "OR source_node_uuid IN (" + selected + ") "
                    "OR target_node_uuid IN (" + selected + ")",
                    parameters=[group_ids, group_ids, group_ids],
                )
                await transaction.run(
                    "DELETE FROM graphiti.graph_node "
                    "WHERE json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
                    parameters=[group_ids],
                )
            await transaction.run("DELETE FROM graphiti.graph_fts_generations")

    async def build_indices_and_constraints(self, executor, delete_existing=False):
        pass

    async def delete_all_indexes(self, executor):
        pass

    async def get_community_clusters(self, executor, group_ids=None):
        return []

    async def determine_entity_community(self, executor, entity):
        pass

    async def get_mentioned_nodes(self, executor, episodes):
        return []

    async def get_communities_by_nodes(self, executor, nodes):
        return []
