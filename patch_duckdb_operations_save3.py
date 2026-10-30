import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

sub1 = """
    async def save(self, executor, node, tx=None):
        props = node.model_dump()
        props_str = json.dumps(props, default=str)
        query = "INSERT INTO graph_node (uuid, label, properties) VALUES (?, ?, ?) ON CONFLICT (uuid) DO UPDATE SET properties=EXCLUDED.properties"
        executor.conn.execute(query, [node.uuid, 'Entity', props_str])
"""
content = content.replace("    async def save(self, executor, node, tx=None): pass", sub1, 1)

sub2 = """
    async def save(self, executor, node, tx=None):
        props = node.model_dump()
        props_str = json.dumps(props, default=str)
        query = "INSERT INTO graph_node (uuid, label, properties) VALUES (?, ?, ?) ON CONFLICT (uuid) DO UPDATE SET properties=EXCLUDED.properties"
        executor.conn.execute(query, [node.uuid, 'Episodic', props_str])
"""
content = content.replace("    async def save(self, executor, node, tx=None): pass", sub2, 1)

sub3 = """
    async def save(self, executor, node, tx=None):
        props = node.model_dump()
        props_str = json.dumps(props, default=str)
        query = "INSERT INTO graph_node (uuid, label, properties) VALUES (?, ?, ?) ON CONFLICT (uuid) DO UPDATE SET properties=EXCLUDED.properties"
        executor.conn.execute(query, [node.uuid, 'Community', props_str])
"""
content = content.replace("    async def save(self, executor, node, tx=None): pass", sub3, 1)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
