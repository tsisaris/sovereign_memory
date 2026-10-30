import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

sub = """
    async def get_mentioned_nodes(self, executor, episodes):
        # MOCK IMPLEMENTATION that returns nodes from graph_node that are targets of MENTIONS edges from the given episodes
        # DuckDB driver specific handling for mock test
        episode_uuids = [episode.uuid for episode in episodes]
        if not episode_uuids: return []
        uuids_str = ','.join([f"'{u}'" for u in episode_uuids])
        # Find target node uuids
        q = f"SELECT target_node_uuid FROM graph_edge WHERE source_node_uuid IN ({uuids_str}) AND label='MENTIONS'"
        target_uuids = [r[0] for r in executor.conn.execute(q).fetchall()]
        if not target_uuids: return []
        tu_str = ','.join([f"'{u}'" for u in target_uuids])
        q2 = f"SELECT properties FROM graph_node WHERE uuid IN ({tu_str})"
        res = executor.conn.execute(q2).fetchall()
        return [EntityNode(**json.loads(r[0])) for r in res]
"""

content = content.replace("    async def get_mentioned_nodes(self, executor, episodes): return []", sub)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
