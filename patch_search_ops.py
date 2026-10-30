import os
import re

search_ops_append = """
    async def get_relevant_nodes(
        self,
        executor: QueryExecutor,
        nodes: list[Any],
        search_filter: Any,
        group_id: str,
        min_score: float,
        limit: int,
    ) -> list[Any]:
        # For the original Cypher calls in search_utils.py we will leave the code there,
        # but change it to call these operations. We will move the driver checks out of search_utils
        # and into the implementations of these operations.
        # But wait, search_utils.py has very specific Cypher.
        raise NotImplementedError()

    async def get_relevant_edges(
        self,
        executor: QueryExecutor,
        edges: list[Any],
        search_filter: Any,
        min_score: float,
        limit: int,
    ) -> list[Any]:
        raise NotImplementedError()

    async def get_edge_invalidation_candidates(
        self,
        executor: QueryExecutor,
        edges: list[Any],
        search_filter: Any,
        min_score: float,
        limit: int,
    ) -> list[Any]:
        raise NotImplementedError()
"""

edge_ops_append = """
    async def filter_existing_duplicate_of_edges(
        self,
        executor: QueryExecutor,
        duplicates_node_uuids: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        raise NotImplementedError()
"""

def append_to_class(file_path, methods_str):
    with open(file_path, 'r') as f:
        content = f.read()
    with open(file_path, 'w') as f:
        f.write(content.rstrip() + "\n" + methods_str + "\n")

for provider in ['neo4j', 'kuzu', 'falkordb', 'neptune']:
    base = f"vendor/graphiti/graphiti_core/driver/{provider}/operations"
    if not os.path.exists(base): continue
    
    search_path = f"{base}/search_ops.py"
    if os.path.exists(search_path):
        append_to_class(search_path, search_ops_append)

    edge_path = f"{base}/entity_edge_ops.py"
    if os.path.exists(edge_path):
        append_to_class(edge_path, edge_ops_append)

