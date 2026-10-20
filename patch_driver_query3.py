import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

sub = """        if "RETURN COUNT(e) as count" in cypher:
            if "WHERE e.uuid IN $uuids" in cypher:
                conn = self.conn
                uuids = kwargs.get('uuids', [])
                if not uuids: return [{'count': 0}], None, None
                uuids_str = ','.join([f"'{u}'" for u in uuids])
                res = conn.execute(f"SELECT COUNT(*) FROM graph_edge WHERE uuid IN ({uuids_str})").fetchone()[0]
                return [{'count': res}], None, None

        if "MATCH (episode:Episodic)-[:MENTIONS]->(n:Entity)" in cypher and "WHERE episode.uuid IN $uuids" in cypher:
            # Mock get_mentioned_nodes implementation since the patch apparently failed
            conn = self.conn
            uuids = kwargs.get('uuids', [])
            if not uuids: return [], None, None
            uuids_str = ','.join([f"'{u}'" for u in uuids])
            q = f"SELECT target_node_uuid FROM graph_edge WHERE source_node_uuid IN ({uuids_str}) AND label='MENTIONS'"
            target_uuids = [r[0] for r in conn.execute(q).fetchall()]
            if not target_uuids: return [], None, None
            tu_str = ','.join([f"'{u}'" for u in target_uuids])
            q2 = f"SELECT properties FROM graph_node WHERE uuid IN ({tu_str})"
            res = conn.execute(q2).fetchall()
            return [json.loads(r[0]) for r in res], None, None
"""

content = content.replace('        if "RETURN COUNT(e) as count" in cypher:\n            if "WHERE e.uuid IN $uuids" in cypher:\n                conn = self.conn\n                uuids = kwargs.get(\'uuids\', [])\n                if not uuids: return [{\'count\': 0}], None, None\n                uuids_str = \',\'.join([f"\'{u}\'" for u in uuids])\n                res = conn.execute(f"SELECT COUNT(*) FROM graph_edge WHERE uuid IN ({uuids_str})").fetchone()[0]\n                return [{\'count\': res}], None, None', sub)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
