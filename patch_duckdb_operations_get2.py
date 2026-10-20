import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

sub = """
    async def get_by_uuid(self, executor, uuid):
        res = executor.conn.execute("SELECT properties FROM graph_node WHERE uuid=?", [uuid]).fetchone()
        if res:
            props = json.loads(res[0])
            if isinstance(props.get('source'), str) and props['source'].startswith('EpisodeType.'):
                props['source'] = props['source'].split('.')[1]
            return EpisodicNode(**props)
        raise Exception("Not found")
"""

content = re.sub(r'    async def get_by_uuid\(self, executor, uuid\):[\s\S]*?raise Exception\("Not found"\)', sub, content, count=1)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)

