"""Create (and cache) the relational database adapter for a provider from its path, name, host, port and credentials.

Goes in: connection settings; comes out: an adapter. It builds an engine object but runs no query
itself.
"""

import importlib.util
from functools import lru_cache

from sqlalchemy import URL

from cognee.shared.lru_cache import DATABASE_MAX_LRU_CACHE_SIZE

from .sqlalchemy.SqlAlchemyAdapter import SQLAlchemyAdapter


@lru_cache(maxsize=DATABASE_MAX_LRU_CACHE_SIZE)
def create_relational_engine(
    db_path: str,
    db_name: str,
    db_host: str,
    db_port: str,
    db_username: str,
    db_password: str,
    db_provider: str,
    database_connect_args: tuple | None = None,
    pool_args: tuple | None = None,
    db_turso_url: str | None = None,
    db_turso_auth_token: str | None = None,
) -> SQLAlchemyAdapter:
    """
    Create a relational database engine based on the specified parameters.
    """
    # Transform pool_args and database_connect_args from tuple of key-value pairs back to dictionary format if they are provided
    database_connect_args = dict(database_connect_args) if database_connect_args else {}
    pool_args = dict(pool_args) if pool_args else {}

    if db_provider == "sqlite":
        raise ConnectionError(
            "DB_PROVIDER=sqlite is not supported: this memory system keeps its relational "
            "data in DuckDB. Set DB_PROVIDER=duckdb."
        )

    elif db_provider == "postgres":
        try:
            # Test if asyncpg is available
            if importlib.util.find_spec("asyncpg") is None:
                raise ImportError("asyncpg is not installed")

            # Handle special characters in username and password like # or @
            connection_string = URL.create(
                "postgresql+asyncpg",
                username=db_username,
                password=db_password,
                host=db_host,
                port=int(db_port),
                database=db_name,
            )

        except ImportError:
            raise ImportError(
                "PostgreSQL dependencies are not installed. Please install with 'pip install cognee\"[postgres]\"' or 'pip install cognee\"[postgres-binary]\"' to use PostgreSQL functionality."
            )

    elif db_provider == "duckdb":
        # Resolve the adapter from our project scope
        from cognee.cognee_duckdb_relational import DuckDBRelationalAdapter

        connection_string = f"duckdb+aio:///{db_path}/{db_name}"

        return DuckDBRelationalAdapter(
            connection_string,
            connect_args=database_connect_args,
            pool_args=pool_args,
        )

    elif db_provider == "turso":
        raise ConnectionError(
            "DB_PROVIDER=turso is not supported: this memory system keeps its relational "
            "data in DuckDB. Set DB_PROVIDER=duckdb."
        )

    else:
        raise ConnectionError("unsupported DB type: " + db_provider)

    return SQLAlchemyAdapter(
        connection_string,
        connect_args=database_connect_args,
        pool_args=pool_args,
    )
