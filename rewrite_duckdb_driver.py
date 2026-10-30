import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

# Let's fix the NotImplementedError on raw Cypher since Kuzu operations use a lot of Cypher directly.
# The user wants "every DuckDB backend operation must execute real DuckDB SQL/semantics"
pass
