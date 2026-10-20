import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

# I need to add load_name_embedding to CommunityNodeOps
content = content.replace(
    "class DuckDBCommunityNodeOps(CommunityNodeOperations):\n    async def save(self, executor, node, tx=None): pass",
    "class DuckDBCommunityNodeOps(CommunityNodeOperations):\n    async def load_name_embedding(self, executor, node): pass\n    async def save(self, executor, node, tx=None): pass"
)

# wait I need to look at CommunityNodeOperations abstract methods
with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
