import re

filepath = 'packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py'
with open(filepath, 'r') as f:
    content = f.read()

lines = content.split('\n')

for i in range(1250, len(lines)):
    if lines[i].startswith("    async def "):
        pass # that's fine
    elif lines[i].startswith("async def "):
        lines[i] = "    " + lines[i]

with open(filepath, 'w') as f:
    f.write('\n'.join(lines))
