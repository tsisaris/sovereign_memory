import re

filepath = 'packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py'
with open(filepath, 'r') as f:
    content = f.read()

# Replace spaces with proper tabs / alignment
lines = content.split("\n")
for i in range(len(lines)):
    line = lines[i]
    if line.strip() == "":
        continue
    # count spaces
    spaces = len(line) - len(line.lstrip(' '))
    if spaces % 4 != 0:
        lines[i] = " " * ((spaces // 4) * 4) + line.lstrip(' ')

with open(filepath, 'w') as f:
    f.write("\n".join(lines))
