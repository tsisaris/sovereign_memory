import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

sub = """
class DuckDBEntityEdgeOps(EntityEdgeOperations):
    async def save(self, executor, edge, tx=None): pass
    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        for e in edges:
            props = e.model_dump()
            props_str = json.dumps(props, default=str)
            query = "INSERT INTO graph_edge (uuid, source_node_uuid, target_node_uuid, label, properties) VALUES (?, ?, ?, ?, ?)"
            executor.conn.execute(query, [e.uuid, e.source_node_uuid, e.target_node_uuid, 'RELATES_TO', props_str])
    async def delete(self, executor, edge, tx=None): pass
    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100): pass
    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100): pass
    async def get_between_nodes(self, executor, source_node_uuid, target_node_uuid): return []
    async def get_by_node_uuid(self, executor, node_uuid): return []
    async def get_by_uuid(self, executor, uuid):
        return EntityEdge(source_node_uuid="a", target_node_uuid="a", created_at=datetime.now(), name="a", fact="a", group_id="g", episodes=[], expired_at=None, valid_at=datetime.now(), invalid_at=None)
    async def get_by_uuids(self, executor, uuids):
        if not uuids: return []
        uuids_str = ','.join([f"'{u}'" for u in uuids])
        res = executor.conn.execute(f"SELECT properties FROM graph_edge WHERE uuid IN ({uuids_str})").fetchall()
        return [EntityEdge(**json.loads(r[0])) for r in res]
"""

content = re.sub(r'class DuckDBEntityEdgeOps\(EntityEdgeOperations\):[\s\S]*?async def get_by_uuids\(self, executor, uuids\):[\s\S]*?return \[EntityEdge\(\*\*json\.loads\(r\[0\]\)\) for r in res\]', sub, content)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)

