import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

content = content.replace("        def setup_schema(self):", "    def setup_schema(self):")

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
