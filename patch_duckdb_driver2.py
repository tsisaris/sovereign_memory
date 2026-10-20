import re
import os

with open("packages/graphiti_duckdb/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

content = content.replace("import kuzu", "import duckdb")
content = re.sub(r"warnings\.warn\([\s\S]*?DeprecationWarning,[\s\S]*?stacklevel=2,[\s\S]*?\)", "", content)
content = content.replace("self.db = kuzu.Database(db)", "self.client = duckdb.connect(db)")
content = content.replace("self.client = kuzu.AsyncConnection(self.db, max_concurrent_queries=max_concurrent_queries)", "")
content = content.replace("KuzuDriverSession", "DuckDBDriverSession")

# Handle AsyncConnection execute which Kuzu used. DuckDB driver here won't be async natively like Kuzu async conn
# we will just run the synchronous DuckDB methods in threads if needed, or just execute them synchronously in the async method.

content = re.sub(r"async def execute_query\([\s\S]*?def session", """async def execute_query(self, cypher_query_: str, **kwargs: Any) -> tuple[list[dict[str, Any]], Any, Any]:
        raise NotImplementedError("DuckDB backend does not support raw Cypher queries. Operations must use the hooks.")

    def session""", content)

content = re.sub(r"async def execute_write\([\s\S]*?class DuckDB", """async def execute_write(self, func, *args, **kwargs):
        return await func(self, *args, **kwargs)

class DuckDB""", content)

with open("packages/graphiti_duckdb/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)

