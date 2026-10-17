"""
Asynchronous SQLAlchemy dialect for DuckDB.

This dialect wraps duckdb_engine's Dialect using the asynchronous bridge.
"""

import duckdb_engine
import sqlalchemy.dialects.postgresql.psycopg2 as ppg
from duckdb_engine import Dialect as DuckDBDialect
from sqlalchemy.dialects.sqlite.aiosqlite import (
    AsyncAdapt_aiosqlite_connection,
    AsyncAdapt_aiosqlite_cursor,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

import duckdb_sqlalchemy_async_bridge


class AsyncAdapt_duckdb_cursor(AsyncAdapt_aiosqlite_cursor):
    """Async cursor."""

    __slots__ = ("connection",)

    def __init__(self, adapt_connection):
        """Init."""
        super().__init__(adapt_connection)
        self.connection = adapt_connection


class AsyncAdapt_duckdb_connection(AsyncAdapt_aiosqlite_connection):
    """Async connection."""

    __slots__ = ("notices",)
    _cursor_cls = AsyncAdapt_duckdb_cursor

    def __init__(self, dbapi, connection):
        """Init."""
        super().__init__(dbapi, connection)
        self.notices = []

    def begin(self):
        """Begin."""
        self.await_(self._connection.begin())


class AsyncAdaptFallback_duckdb_connection(AsyncAdapt_duckdb_connection):
    """Fallback connection wrapper."""

    __slots__ = ()

    def __init__(self, dbapi, connection_awaitable):
        """Init."""
        super().__init__(dbapi, None)
        self._connection = self.await_(connection_awaitable)


class DuckDBAsyncDialect(DuckDBDialect):
    """Dialect."""

    name = "duckdb"
    driver = "aio"
    supports_statement_cache = True
    is_async = True
    paramstyle = "qmark"

    @classmethod
    def import_dbapi(cls):
        """Import DBAPI."""
        return duckdb_sqlalchemy_async_bridge

    @classmethod
    def get_pool_class(cls, url):
        """Get pool class."""
        if url.database == ":memory:":
            return NullPool
        return AsyncAdaptedQueuePool

    def get_driver_connection(self, connection):
        """Get driver connection."""
        return connection._connection

    def connect(self, *cargs, **cparams):
        """Connect."""
        creator_fn = cparams.pop("async_creator_fn", self.dbapi.connect)
        return AsyncAdaptFallback_duckdb_connection(self.dbapi, creator_fn(*cargs, **cparams))

    def do_rollback(self, dbapi_connection):
        """Rollback."""
        dbapi_connection.rollback()

    def do_commit(self, dbapi_connection):
        """Commit."""
        dbapi_connection.commit()

    def do_begin(self, dbapi_connection):
        """Begin."""
        dbapi_connection.begin()


def patched_post_exec(self, *args, **kwargs):
    """Patched post_exec."""
    pass


duckdb_engine.Dialect._log_notices = patched_post_exec
ppg.PGExecutionContext_psycopg2.post_exec = patched_post_exec
