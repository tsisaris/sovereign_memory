"""Adapt the buffered asynchronous DuckDB bridge to SQLAlchemy's async engine."""

from duckdb_engine import Dialect as DuckDBDialect
from sqlalchemy.connectors.asyncio import AsyncAdapt_dbapi_connection, AsyncAdapt_dbapi_cursor
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from sqlalchemy.util.concurrency import await_only

import duckdb_sqlalchemy_async_bridge


class AsyncAdapt_duckdb_cursor(AsyncAdapt_dbapi_cursor):
    """Expose a buffered bridge cursor through SQLAlchemy's synchronous facade."""

    def _aenter_cursor(self, cursor):
        """Resolve the bridge's asynchronous logical-cursor factory."""
        return self.await_(cursor)

    @property
    def connection(self):
        """Expose the owner for inherited PostgreSQL notice processing."""
        return self._adapt_connection


class AsyncAdapt_duckdb_connection(AsyncAdapt_dbapi_connection):
    """Use the normal SQLAlchemy async adapter and the bridge's transaction gate."""

    _cursor_cls = AsyncAdapt_duckdb_cursor

    @property
    def notices(self):
        """Return this connection's notices to the inherited execution context."""
        return self._connection.notices

    def begin(self):
        """Begin on the same handle that all logical cursors execute against."""
        self.await_(self._connection.begin())

    def cursor(self, server_side=False):
        """Return a buffered cursor; reject unimplemented server-side streaming."""
        if server_side:
            raise duckdb_sqlalchemy_async_bridge.NotSupportedError("Streaming is not supported")
        return super().cursor()


class DuckDBAsyncDialect(DuckDBDialect):
    """Retain DuckDB SQL compilation while adapting connections asynchronously."""

    name = "duckdb"
    driver = "aio"
    # Inherit the upstream safety policy until compiler cache behavior is proven.
    supports_statement_cache = False
    is_async = True
    paramstyle = "qmark"

    @classmethod
    def import_dbapi(cls):
        """Return the bridge's DB-API exception and connection interface."""
        return duckdb_sqlalchemy_async_bridge

    @classmethod
    def get_pool_class(cls, url):
        """Keep memory connections independent and queue file-backed connections."""
        if not url.database or url.database == ":memory:":
            return NullPool
        return AsyncAdaptedQueuePool

    def connect(self, *cargs, **cparams):
        """Await the configured async creator inside SQLAlchemy's greenlet."""
        creator = cparams.pop("async_creator_fn", self.import_dbapi().connect)
        return AsyncAdapt_duckdb_connection(
            self.import_dbapi(), await_only(creator(*cargs, **cparams))
        )
