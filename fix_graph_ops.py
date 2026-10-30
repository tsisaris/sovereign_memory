import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations/graph_ops.py", "r") as f:
    content = f.read()

content = content.replace("await executor.execute_query('MATCH (n) DETACH DELETE n')", "pass")
content = re.sub(r"await executor\.execute_query\([\s\S]*?MATCH \(n\)[\s\S]*?DETACH DELETE n[\s\S]*?group_ids=group_ids,[\s\S]*?\)", "pass", content)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations/graph_ops.py", "w") as f:
    f.write(content)
