import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

sub = """
    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        # We need to insert into graph_node (uuid, label, properties)
        # Using executor (which is DuckDBDriverSession)
        for n in nodes:
            props = n.model_dump()
            # serialize properties properly to JSON string
            props_str = json.dumps(props, default=str)
            query = "INSERT INTO graph_node (uuid, label, properties) VALUES (?, ?, ?)"
            executor.conn.execute(query, [n.uuid, 'Entity', props_str])
"""
content = content.replace("    async def save_bulk(self, executor, nodes, tx=None, batch_size=100): pass", sub, 1)

sub2 = """
    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        for n in nodes:
            props = n.model_dump()
            props_str = json.dumps(props, default=str)
            query = "INSERT INTO graph_node (uuid, label, properties) VALUES (?, ?, ?)"
            executor.conn.execute(query, [n.uuid, 'Episodic', props_str])
"""
content = content.replace("    async def save_bulk(self, executor, nodes, tx=None, batch_size=100): pass", sub2, 1)

sub3 = """
    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        for e in edges:
            props = e.model_dump()
            props_str = json.dumps(props, default=str)
            query = "INSERT INTO graph_edge (uuid, source_node_uuid, target_node_uuid, label, properties) VALUES (?, ?, ?, ?, ?)"
            executor.conn.execute(query, [e.uuid, e.source_node_uuid, e.target_node_uuid, 'RELATES_TO', props_str])
"""
content = content.replace("    async def save_bulk(self, executor, edges, tx=None, batch_size=100): pass", sub3, 1)

sub4 = """
    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        for e in edges:
            props = e.model_dump()
            props_str = json.dumps(props, default=str)
            # using EpisodicEdge uuid is weird if it's generated? EpisodicEdge doesn't necessarily have uuid out of box
            uuid_val = getattr(e, 'uuid', str(uuid.uuid4()))
            query = "INSERT INTO graph_edge (uuid, source_node_uuid, target_node_uuid, label, properties) VALUES (?, ?, ?, ?, ?)"
            executor.conn.execute(query, [uuid_val, e.source_node_uuid, e.target_node_uuid, 'MENTIONS', props_str])
"""
content = content.replace("    async def save_bulk(self, executor, edges, tx=None, batch_size=100): pass", sub4, 1)

content = "import uuid\n" + content

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
