import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

sub = """
class DuckDBCommunityNodeOps(CommunityNodeOperations):
    async def save(self, executor, node, tx=None): pass
    async def load_name_embedding(self, executor, node): pass
"""

content = re.sub(r'class DuckDBCommunityNodeOps\(CommunityNodeOperations\):\s*async def save\(self, executor, node, tx=None\): pass', sub, content)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
