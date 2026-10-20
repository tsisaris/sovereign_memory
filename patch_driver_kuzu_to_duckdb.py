import re
with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

content = content.replace("conn = kuzu.Connection(self.db)", "pass # DuckDB schema will be defined via SQL hooks")
content = content.replace("conn.execute(SCHEMA_QUERIES)", "")
content = content.replace("conn.close()", "")
content = content.replace("self.setup_schema()", "self.setup_schema()")

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
