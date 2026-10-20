import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

# I apparently overwrote the file instead of modifying it when I ran rewrite_duckdb_driver.py earlier. Let me just rebuild the file.
pass
