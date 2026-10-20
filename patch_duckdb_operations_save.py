import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/operations.py", "r") as f:
    content = f.read()

# We need to implement actual `save_bulk` using DuckDB SQL so that nodes and edges actually exist for `get_node_count`.
# Wait, this requires full SQL logic for insert statements with JSON serialization etc.
pass
