with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

content = content.replace(
    "class DuckDBEntityEdgeOps(EntityEdgeOperations):\n    async def save(self, executor, edge, tx=None): pass",
    "class DuckDBEntityEdgeOps(EntityEdgeOperations):\n    async def get_between_nodes(self, executor, source_node_uuid, target_node_uuid): return []\n    async def get_by_node_uuid(self, executor, node_uuid): return []\n    async def save(self, executor, edge, tx=None): pass"
)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
