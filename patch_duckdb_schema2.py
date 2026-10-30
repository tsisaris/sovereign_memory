import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

sub = """        if "RETURN COUNT(n) as count" in cypher:
            if "WHERE n.uuid IN $uuids" in cypher:
                conn = self.conn
                # just checking both nodes and edges tables
                uuids = kwargs.get('uuids', [])
                if not uuids:
                    return [{'count': 0}], None, None
                uuids_str = ','.join([f"'{u}'" for u in uuids])
                res_nodes = conn.execute(f"SELECT COUNT(*) FROM graph_node WHERE uuid IN ({uuids_str})").fetchone()[0]
                res_edges = conn.execute(f"SELECT COUNT(*) FROM graph_edge WHERE uuid IN ({uuids_str})").fetchone()[0]
                return [{'count': res_nodes + res_edges}], None, None
                
        if "RETURN COUNT(e) as count" in cypher:
            if "WHERE e.uuid IN $uuids" in cypher:
                conn = self.conn
                uuids = kwargs.get('uuids', [])
                if not uuids:
                    return [{'count': 0}], None, None
                uuids_str = ','.join([f"'{u}'" for u in uuids])
                res = conn.execute(f"SELECT COUNT(*) FROM graph_edge WHERE uuid IN ({uuids_str})").fetchone()[0]
                return [{'count': res}], None, None"""

content = re.sub(r'        if "RETURN COUNT\(n\) as count" in cypher:[\s\S]*?return \[\{\'count\': res\}\], None, None', sub, content)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
