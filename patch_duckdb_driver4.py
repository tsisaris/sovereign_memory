import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

sub = """        from .operations import DuckDBEntityNodeOps, DuckDBEpisodeNodeOps, DuckDBCommunityNodeOps, DuckDBSagaNodeOps, DuckDBEntityEdgeOps, DuckDBEpisodicEdgeOps, DuckDBCommunityEdgeOps, DuckDBHasEpisodeEdgeOps, DuckDBNextEpisodeEdgeOps, DuckDBSearchOps, DuckDBGraphOps
        self._entity_node_ops = DuckDBEntityNodeOps()
        self._episode_node_ops = DuckDBEpisodeNodeOps()
        self._community_node_ops = DuckDBCommunityNodeOps()
        self._saga_node_ops = DuckDBSagaNodeOps()
        self._entity_edge_ops = DuckDBEntityEdgeOps()
        self._episodic_edge_ops = DuckDBEpisodicEdgeOps()
        self._community_edge_ops = DuckDBCommunityEdgeOps()
        self._has_episode_edge_ops = DuckDBHasEpisodeEdgeOps()
        self._next_episode_edge_ops = DuckDBNextEpisodeEdgeOps()
        self._search_ops = DuckDBSearchOps()
        self._graph_ops = DuckDBGraphOps()"""

content = content.replace("# We will dynamically set these", sub)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
