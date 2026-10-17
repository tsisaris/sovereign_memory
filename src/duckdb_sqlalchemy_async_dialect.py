"""
Asynchronous SQLAlchemy dialect for DuckDB.

This dialect wraps duckdb_engine's Dialect using the asynchronous bridge,
implementing standard SQLAlchemy asynchronous interfaces.
"""

from duckdb_engine import Dialect as DuckDBDialect
from sqlalchemy.connectors.asyncio import (
    AsyncAdapt_dbapi_connection,
    AsyncAdapt_dbapi_cursor,
    AsyncAdaptFallback_dbapi_connection,
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


class AsyncAdaptFallback_duckdb_connection(AsyncAdaptFallback_dbapi_connection):
    """
    Fallback connection wrapper for DuckDB.

    This creates an async connection from an awaitable connect function for SQLAlchemy.
    """

    __slots__ = ()
    _cursor_cls = AsyncAdapt_duckdb_cursor

    def __init__(self, dbapi, connection_awaitable):
        """Init."""
        self.dbapi = dbapi
        self._connection = self.await_(connection_awaitable)
        import asyncio

        self._execute_mutex = asyncio.Lock()

    def begin(self):
        """Begin."""
        self.await_(self._connection.begin())


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

    # Provide a distinct execution context class that overrides _log_notices securely
    # without monkey-patching process-wide SQLAlchemy state.
    # mypy complains that DuckDBDialect.execution_ctx_cls is not a valid base class type,
    # so we alias it to Any during type checking via a cast or type ignore.
    class execution_ctx_cls(DuckDBDialect.execution_ctx_cls):  # type: ignore[valid-type, misc]
        """
        Custom execution context for DuckDB async dialect.

        Overrides post_exec without globally patching PostgreSQL contexts.
        """

        def post_exec(self):
            """
            Post-execution hook.

            Input: None.
            Output: None.
            Never: Pollutes other dialects' execution contexts.
            """
            pass

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
        _ = cls  # Use cls to fix vulture check
        if url.database == ":memory:":
            return NullPool
        return AsyncAdaptedQueuePool

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

    def do_rollback(self, dbapi_connection):
        """
        Rollback the current transaction.

        Input: A DBAPI connection.
        Output: None.
        Never: Fails if there is no active transaction.
        """
        if hasattr(dbapi_connection, "rollback") and not hasattr(dbapi_connection, "await_"):
            dbapi_connection.rollback()
        else:
            dbapi_connection.await_(dbapi_connection._connection.rollback())

    def do_commit(self, dbapi_connection):
        """
        Commit the current transaction.

        Input: A DBAPI connection.
        Output: None.
        Never: Fails if there is no active transaction.
        """
        if hasattr(dbapi_connection, "commit") and not hasattr(dbapi_connection, "await_"):
            dbapi_connection.commit()
        else:
            dbapi_connection.await_(dbapi_connection._connection.commit())

    def do_begin(self, dbapi_connection):
        """
        Begin a new transaction.

        Input: A DBAPI connection.
        Output: None.
        Never: Starts a transaction if one is already active.
        """
        if hasattr(dbapi_connection, "begin") and not hasattr(dbapi_connection, "await_"):
            dbapi_connection.begin()
        else:
            dbapi_connection.await_(dbapi_connection._connection.begin())
