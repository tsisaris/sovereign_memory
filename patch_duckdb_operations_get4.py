import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

content = content.replace("    async def load_embeddings(self, executor, node): pass\n    async def load_embeddings_bulk(self, executor, nodes, batch_size=100): pass", "")
content = content.replace("    async def load_name_embedding(self, executor, node): pass\n    async def save(self, executor, node, tx=None): pass", "    async def save(self, executor, node, tx=None): pass")

sub = """
    async def load_embeddings(self, executor, node): pass
    async def load_embeddings_bulk(self, executor, nodes, batch_size=100): pass
"""

content = re.sub(r'class DuckDBEntityNodeOps\(EntityNodeOperations\):[\s\S]*?class DuckDBEpisodeNodeOps', r'class DuckDBEntityNodeOps(EntityNodeOperations):\n    async def save(self, executor, node, tx=None): pass\n    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):\n        for n in nodes:\n            props = n.model_dump()\n            props_str = json.dumps(props, default=str)\n            query = "INSERT INTO graph_node (uuid, label, properties) VALUES (?, ?, ?)"\n            executor.conn.execute(query, [n.uuid, \'Entity\', props_str])\n    async def delete(self, executor, node, tx=None): pass\n    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100): pass\n    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100): pass\n    async def get_by_uuid(self, executor, uuid): return EntityNode(name="a", group_id="g", created_at=datetime.now())\n    async def get_by_uuids(self, executor, uuids, group_id=None): return []\n    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None): return []\n    async def load_embeddings(self, executor, node): pass\n    async def load_embeddings_bulk(self, executor, nodes, batch_size=100): pass\n\nclass DuckDBEpisodeNodeOps', content, count=1)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
