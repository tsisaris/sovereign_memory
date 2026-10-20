import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

content = content.replace("self._entity_node_ops = DuckDBEntityNodeOperations()", "from .operations import DuckDBEntityNodeOps, DuckDBEpisodeNodeOps, DuckDBCommunityNodeOps, DuckDBSagaNodeOps, DuckDBEntityEdgeOps, DuckDBEpisodicEdgeOps, DuckDBCommunityEdgeOps, DuckDBHasEpisodeEdgeOps, DuckDBNextEpisodeEdgeOps, DuckDBSearchOps, DuckDBGraphOps\n        self._entity_node_ops = DuckDBEntityNodeOps()")

content = content.replace("self._episode_node_ops = DuckDBEpisodeNodeOperations()", "self._episode_node_ops = DuckDBEpisodeNodeOps()")
content = content.replace("self._community_node_ops = DuckDBCommunityNodeOperations()", "self._community_node_ops = DuckDBCommunityNodeOps()")
content = content.replace("self._saga_node_ops = DuckDBSagaNodeOperations()", "self._saga_node_ops = DuckDBSagaNodeOps()")
content = content.replace("self._entity_edge_ops = DuckDBEntityEdgeOperations()", "self._entity_edge_ops = DuckDBEntityEdgeOps()")
content = content.replace("self._episodic_edge_ops = DuckDBEpisodicEdgeOperations()", "self._episodic_edge_ops = DuckDBEpisodicEdgeOps()")
content = content.replace("self._community_edge_ops = DuckDBCommunityEdgeOperations()", "self._community_edge_ops = DuckDBCommunityEdgeOps()")
content = content.replace("self._has_episode_edge_ops = DuckDBHasEpisodeEdgeOperations()", "self._has_episode_edge_ops = DuckDBHasEpisodeEdgeOps()")
content = content.replace("self._next_episode_edge_ops = DuckDBNextEpisodeEdgeOperations()", "self._next_episode_edge_ops = DuckDBNextEpisodeEdgeOps()")
content = content.replace("self._search_ops = DuckDBSearchOperations()", "self._search_ops = DuckDBSearchOps()")
content = content.replace("self._graph_ops = DuckDBGraphMaintenanceOperations()", "self._graph_ops = DuckDBGraphOps()")

# The driver also needs the return types to match the base classes
imports = """
from graphiti_core.driver.operations.entity_node_ops import EntityNodeOperations
from graphiti_core.driver.operations.episode_node_ops import EpisodeNodeOperations
from graphiti_core.driver.operations.community_node_ops import CommunityNodeOperations
from graphiti_core.driver.operations.saga_node_ops import SagaNodeOperations
from graphiti_core.driver.operations.entity_edge_ops import EntityEdgeOperations
from graphiti_core.driver.operations.episodic_edge_ops import EpisodicEdgeOperations
from graphiti_core.driver.operations.community_edge_ops import CommunityEdgeOperations
from graphiti_core.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
from graphiti_core.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
from graphiti_core.driver.operations.search_ops import SearchOperations
from graphiti_core.driver.operations.graph_ops import GraphMaintenanceOperations
"""
content = imports + "\n" + content

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
