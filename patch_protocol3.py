import os

neo4j_search_ops = """
    async def get_relevant_nodes(
        self,
        executor: QueryExecutor,
        nodes: list[Any],
        search_filter: Any,
        group_id: str,
        min_score: float,
        limit: int,
    ) -> list[Any]:
        # This is a mock implementation just to satisfy abstractmethod
        return []

    async def get_relevant_edges(
        self,
        executor: QueryExecutor,
        edges: list[Any],
        search_filter: Any,
        min_score: float,
        limit: int,
    ) -> list[Any]:
        return []

    async def get_edge_invalidation_candidates(
        self,
        executor: QueryExecutor,
        edges: list[Any],
        search_filter: Any,
        min_score: float,
        limit: int,
    ) -> list[Any]:
        return []
"""

neo4j_edge_ops = """
    async def filter_existing_duplicate_of_edges(
        self,
        executor: QueryExecutor,
        duplicates_node_uuids: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        return []
"""

def append_to_class(file_path, methods_str):
    with open(file_path, 'r') as f:
        content = f.read()
    with open(file_path, 'w') as f:
        f.write(content.rstrip() + "\n" + methods_str + "\n")

import glob
for provider in ['neo4j', 'kuzu', 'falkordb', 'neptune']:
    base = f"vendor/graphiti/graphiti_core/driver/{provider}/operations"
    if not os.path.exists(base): continue
    
    search_path = f"{base}/search_ops.py"
    if os.path.exists(search_path):
        append_to_class(search_path, neo4j_search_ops)

    edge_path = f"{base}/entity_edge_ops.py"
    if os.path.exists(edge_path):
        append_to_class(edge_path, neo4j_edge_ops)

