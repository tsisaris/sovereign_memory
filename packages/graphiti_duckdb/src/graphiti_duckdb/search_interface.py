"""Expose the driver's native search operations through Graphiti's SearchInterface hook.

``graphiti_core.search`` consults ``driver.search_interface`` before falling back to
Cypher, so this adapter is what makes ``Graphiti.search`` and ``search_`` run on DuckDB.
Each method forwards to the one implementation in ``DuckDBSearchOps`` on the same
driver handle; it adds no store, connection or result of its own.
"""

from graphiti_core.driver.search_interface.search_interface import SearchInterface

from .search_ops import DuckDBSearchOps


class DuckDBSearchInterface(SearchInterface):
    """Forward each maintained search hook to ``driver.search_ops`` with its own arguments."""

    async def edge_fulltext_search(self, driver, query, search_filter, group_ids=None, limit=100):
        """Return keyword-matched entity edges from native BM25 scoring."""
        return await driver.search_ops.edge_fulltext_search(
            driver, query, search_filter, group_ids, limit
        )

    async def edge_similarity_search(
        self,
        driver,
        search_vector,
        source_node_uuid,
        target_node_uuid,
        search_filter,
        group_ids=None,
        limit=100,
        min_score=0.7,
    ):
        """Return entity edges ranked by normalized cosine of their fact embeddings."""
        return await driver.search_ops.edge_similarity_search(
            driver,
            search_vector,
            source_node_uuid,
            target_node_uuid,
            search_filter,
            group_ids,
            limit,
            min_score,
        )

    async def node_fulltext_search(self, driver, query, search_filter, group_ids=None, limit=100):
        """Return keyword-matched entities from native BM25 scoring."""
        return await driver.search_ops.node_fulltext_search(
            driver, query, search_filter, group_ids, limit
        )

    async def node_similarity_search(
        self, driver, search_vector, search_filter, group_ids=None, limit=100, min_score=0.7
    ):
        """Return entities ranked by normalized cosine of their name embeddings."""
        return await driver.search_ops.node_similarity_search(
            driver, search_vector, search_filter, group_ids, limit, min_score
        )

    async def episode_fulltext_search(
        self, driver, query, search_filter, group_ids=None, limit=100
    ):
        """Return keyword-matched episodes from native BM25 scoring."""
        return await driver.search_ops.episode_fulltext_search(
            driver, query, search_filter, group_ids, limit
        )

    async def edge_bfs_search(
        self,
        driver,
        bfs_origin_node_uuids,
        bfs_max_depth,
        search_filter,
        group_ids=None,
        limit=100,
    ):
        """Return entity edges found by the bounded recursive walk from the origins."""
        return await driver.search_ops.edge_bfs_search(
            driver, bfs_origin_node_uuids, bfs_max_depth, search_filter, group_ids, limit
        )

    async def node_bfs_search(
        self,
        driver,
        bfs_origin_node_uuids,
        search_filter,
        bfs_max_depth,
        group_ids=None,
        limit=100,
    ):
        """Return entities found by the bounded recursive walk from the origins."""
        return await driver.search_ops.node_bfs_search(
            driver, bfs_origin_node_uuids, search_filter, bfs_max_depth, group_ids, limit
        )

    async def community_fulltext_search(self, driver, query, group_ids=None, limit=100):
        """Return keyword-matched communities from native BM25 scoring."""
        return await driver.search_ops.community_fulltext_search(driver, query, group_ids, limit)

    async def community_similarity_search(
        self, driver, search_vector, group_ids=None, limit=100, min_score=0.6
    ):
        """Return communities ranked by normalized cosine of their name embeddings."""
        return await driver.search_ops.community_similarity_search(
            driver, search_vector, group_ids, limit, min_score
        )

    async def get_embeddings_for_communities(self, driver, communities):
        """Return stored name embeddings keyed by community UUID."""
        return await driver.search_ops.community_embeddings(driver, communities)

    async def node_distance_reranker(self, driver, node_uuids, center_node_uuid, min_score=0):
        """Return ``(uuids, scores)`` ordered by graph distance to the centre node."""
        return await driver.search_ops.rank_by_distance(
            driver, node_uuids, center_node_uuid, min_score
        )

    async def episode_mentions_reranker(self, driver, node_uuids, min_score=0):
        """Return ``(uuids, counts)`` ordered by how many episodes mention each node."""
        return await driver.search_ops.rank_by_mentions(driver, node_uuids, min_score)

    def build_node_search_filters(self, search_filters):
        """Return the native ``(clauses, values)`` entity filter; no database is needed."""
        return DuckDBSearchOps().build_node_search_filters(search_filters)

    def build_edge_search_filters(self, search_filters):
        """Return the native ``(clauses, values)`` entity-edge filter; no database is needed."""
        return DuckDBSearchOps().build_edge_search_filters(search_filters)
