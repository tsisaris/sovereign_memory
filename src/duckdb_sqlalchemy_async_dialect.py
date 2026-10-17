"""
Asynchronous SQLAlchemy dialect for DuckDB.

This dialect wraps duckdb_engine's Dialect using the asynchronous bridge,
implementing standard SQLAlchemy asynchronous interfaces.
"""

from duckdb_engine import Dialect as DuckDBDialect
from sqlalchemy.connectors.asyncio import (
    AsyncAdapt_dbapi_connection,
    AsyncAdapt_dbapi_cursor,
    await_fallback,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

import duckdb_sqlalchemy_async_bridge


class AsyncAdapt_duckdb_cursor(AsyncAdapt_dbapi_cursor):
    """
    Async cursor adapter for DuckDB.

    This adapts the DuckDB async cursor to SQLAlchemy's expected async cursor interface.
    """

    def _aenter_cursor(self, cursor):
        """
        Await the cursor generation correctly.
        """
        if hasattr(cursor, "__aenter__"):
            return self.await_(cursor.__aenter__())
        return self.await_(cursor)


class AsyncAdapt_duckdb_connection(AsyncAdapt_dbapi_connection):
    """
    Async connection adapter for DuckDB.

    This adapts the DuckDB async connection to SQLAlchemy's expected async connection interface.
    """

    _cursor_cls = AsyncAdapt_duckdb_cursor

    def begin(self):
        """Begin."""
        self.await_(self._connection.begin())

    def rollback(self):
        """Rollback."""
        self.await_(self._connection.rollback())

    def commit(self):
        """Commit."""
        self.await_(self._connection.commit())


class AsyncAdaptFallback_duckdb_connection(AsyncAdapt_duckdb_connection):
    """
    Fallback connection wrapper for DuckDB.

    This creates an async connection from an awaitable connect function for SQLAlchemy.
    """

    __slots__ = ()

    await_ = staticmethod(await_fallback)  # type: ignore[assignment]

    def __init__(self, dbapi, connection_awaitable):
        """
        Initialize the fallback adapter.

        Input: A dbapi instance and a coroutine to resolve a connection.
        Output: None.
        Never: Leaves the connection unresolved.
        """
        self.dbapi = dbapi
        self._connection = self.await_(connection_awaitable)
        import asyncio

        self._execute_mutex = asyncio.Lock()


# Define execution context clearly to avoid monkey patching global psycopg2.
# We map it securely without monkey patching process-wide state.
class DuckDBAsyncExecutionContext(DuckDBDialect.execution_ctx_cls):  # type: ignore[valid-type, name-defined, misc]
    """
    Custom execution context for DuckDB async dialect.

    Overrides post_exec without globally patching PostgreSQL contexts.
    duckdb_engine's dialect overrides post_exec to read notices from the connection.
    For this to be safe without breaking anything, we just pass since duckdb does not generate
    these standard notices that psycopg2 reads.
    """

    def post_exec(self):
        """
        Post-execution hook.

        Input: None.
        Output: None.
        Never: Pollutes other dialects' execution contexts.
        """
        pass


class DuckDBAsyncDialect(DuckDBDialect):
    """
    Async dialect for DuckDB.

    This provides the dialect settings and adapters required for SQLAlchemy
    to connect and interact asynchronously with a DuckDB database.
    """

    name = "duckdb"
    driver = "aio"
    supports_statement_cache = True
    is_async = True
    paramstyle = "qmark"

    execution_ctx_cls = DuckDBAsyncExecutionContext

    @classmethod
    def import_dbapi(cls):
        """
        Import the DBAPI module.

        Input: None.
        Output: The duckdb_sqlalchemy_async_bridge module.
        Never: Returns a synchronous driver directly.
        """
        return duckdb_sqlalchemy_async_bridge

    @classmethod
    def get_pool_class(cls, url):
        """
        Get the pool class for the given URL.

        Input: The connection URL.
        Output: NullPool for memory databases, AsyncAdaptedQueuePool for others.
        Never: Shares a memory database across multiple unrelated connections.
        """
        if url.database == ":memory:":
            return NullPool
        return AsyncAdaptedQueuePool

    @classmethod
    def get_async_dialect_cls(cls, url):
        """
        Get the async dialect class.

        Input: Connection URL.
        Output: This dialect class itself.
        Never: Returns a synchronous dialect accidentally.
        """
        return cls

    def get_driver_connection(self, connection):
        """
        Get the raw driver connection from an adapted connection.

        Input: An adapted connection object.
        Output: The underlying bridge connection object.
        Never: Returns a connection that isn't the underlying driver connection.
        """
        return connection._connection

    def connect(self, *cargs, **cparams):
        """
        Establish a connection.

        Input: Connection arguments.
        Output: An AsyncAdaptFallback_duckdb_connection instance.
        Never: Connects synchronously to the database.
        """
        creator_fn = cparams.pop("async_creator_fn", self.import_dbapi().connect)
        return AsyncAdaptFallback_duckdb_connection(
            self.import_dbapi(),
            creator_fn(*cargs, **cparams),
        )
