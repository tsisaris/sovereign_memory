import re

filepath = "packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py"
with open(filepath, "r") as f:
    content = f.read()

# Replace all "\n    async def " with "\n    async def " to make sure they are aligned
lines = content.split("\n")
in_class = False
for i, line in enumerate(lines):
    if line.startswith("class DuckDBAdapter"):
        in_class = True
    if in_class and line.startswith("async def"):
        # Incorrectly aligned
        lines[i] = "    " + line
    elif in_class and line.startswith("    async def get_node_delete_data"):
        # It's indented right. But wait, maybe the previous method was unindented or missing parenthesis.
        pass

# The easiest way to fix all these syntax errors is to use black.
# But black couldn't run because of syntax error.
# "invalid-syntax: unindent does not match any outer indentation level" usually means mixed spaces and tabs or block closures.
# Let's see the previous function around line 1143
