import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

content = content.replace("executor.conn.execute(query, [n.uuid, \\'Entity\\', props_str])", "executor.conn.execute(query, [n.uuid, 'Entity', props_str])")

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "w") as f:
    f.write(content)
