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
        connection_string = f"sqlite+aiosqlite:///{db_path}/{db_name}"

    elif db_provider == "postgres":
        try:
            # Test if asyncpg is available
            import asyncpg

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
        from cognee.infrastructure.databases.turso import require_turso

        # Probe the driver here: the adapter module imports it lazily, so importing
        # the module alone would not surface a missing turso extra.
        require_turso()

        if db_turso_url or db_turso_auth_token:
            raise OSError(
                "Remote Turso databases are not supported by the local Turso backend in this "
                "version (DB_TURSO_URL / DB_TURSO_AUTH_TOKEN are set). Unset them to use a "
                "local Turso database file under DB_PATH, or pick another DB_PROVIDER. These "
                "settings are deprecated leftovers of the former libSQL replica adapter and "
                "will be removed in a future release."
            )

        from .sqlalchemy.TursoAdapter import TursoAdapter

        # The Turso rewrite engine (pyturso) drives the query path, migrations and
        # the sqlite-dialect code paths through cognee's own SQLAlchemy dialect.
        return TursoAdapter(
            f"{db_path}/{db_name}",
            connect_args=database_connect_args,
            pool_args=pool_args,
        )

    else:
        raise ConnectionError("unsupported DB type: " + db_provider)

    return SQLAlchemyAdapter(
        connection_string,
        connect_args=database_connect_args,
        pool_args=pool_args,
    )
