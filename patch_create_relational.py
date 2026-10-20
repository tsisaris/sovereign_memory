file_path = "packages/cognee_duckdb/cognee/infrastructure/databases/relational/create_relational_engine.py"
with open(file_path, "r") as f:
    content = f.read()

import re

content = re.sub(
    r'    if db_provider == "sqlite":\n        connection_string = f"sqlite\+aiosqlite:///{db_path}/{db_name}"\n',
    '    if db_provider == "sqlite":\n        connection_string = f"sqlite+aiosqlite:///{db_path}/{db_name}"\n    elif db_provider == "duckdb":\n        from cognee.cognee_duckdb_relational import DuckDBRelationalAdapter\n        return DuckDBRelationalAdapter(f"duckdb+aio:///{db_path}/{db_name}", connect_args=database_connect_args, pool_args=pool_args)\n',
    content
)

with open(file_path, "w") as f:
    f.write(content)
