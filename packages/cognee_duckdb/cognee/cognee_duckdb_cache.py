"""DuckDB cache adapter for Cognee."""

import logging

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from cognee.infrastructure.databases.cache.sql.SqlCacheAdapter import SqlCacheAdapter

logger = logging.getLogger(__name__)


class DuckDBCacheAdapter(SqlCacheAdapter):
    """Adapt Cognee's SQL cache logic for DuckDB."""

    def __init__(self, connection_string: str):
        super().__init__(connection_string)
        # Force SQLite dialect mode paths in SqlCacheAdapter to trigger for duckdb,
        # since duckdb SQL behaves similarly to sqlite for basic operations,
        # but skip the SQLite specific PRAGMAs.
        self._is_postgres = False

    async def _ensure_initialized(self) -> None:
        """Skip SQLite PRAGMAs but initialize tables."""
        if not getattr(self, "_initialized", False):
            # Patch SQLAlchemy events to NOT emit PRAGMAs for duckdb
            import sqlalchemy
            from sqlalchemy.engine import Engine

            # Unregister any SQLite pragma listeners that SqlCacheAdapter might have attached
            if sqlalchemy.event.contains(Engine, "connect", self._set_sqlite_pragmas_if_exists):
                sqlalchemy.event.remove(Engine, "connect", self._set_sqlite_pragmas_if_exists)

            await super()._ensure_initialized()

    def _set_sqlite_pragmas_if_exists(self, dbapi_connection, connection_record):
        pass  # Empty handler in case it was used

    def get_upsert_insert(self):
        """DuckDB supports PostgreSQL-style ON CONFLICT or SQLite-style.
        DuckDB's ON CONFLICT DO UPDATE works similarly to Postgres.
        DuckDB engine for SQLAlchemy supports SQLite's insert syntax via translation."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        # Use Postgres-style upserts since DuckDB natively supports them well via postgresql dialect syntax
        return sqlite_insert
