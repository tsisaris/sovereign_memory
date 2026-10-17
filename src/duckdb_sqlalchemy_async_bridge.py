"""
Asynchronous bridge for DuckDB's Python DB-API.

This module provides awaitable Connection and Cursor objects over DuckDB's
synchronous DB-API connection. It executes each DB-API call inside a single
worker thread per connection, mimicking the aiosqlite pattern to avoid blocking
the asyncio event loop.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

import duckdb

T = TypeVar("T")

# Map duckdb exceptions to DB-API 2.0 standard exception names.
# DuckDB does not define InterfaceError, so we map it to Error.
Warning = duckdb.Warning
Error = duckdb.Error
InterfaceError = duckdb.Error
DatabaseError = duckdb.DatabaseError
DataError = duckdb.DataError
OperationalError = duckdb.OperationalError
IntegrityError = duckdb.IntegrityError
InternalError = duckdb.InternalError
ProgrammingError = duckdb.ProgrammingError
NotSupportedError = duckdb.NotSupportedError

paramstyle = "qmark"


class AsyncCursor:
    """
    Awaitable cursor for DuckDB.

    This wraps a DuckDB DB-API cursor and delegates calls to the connection's
    worker thread.
    """

    def __init__(self, conn: "AsyncConnection", cursor: duckdb.DuckDBPyConnection):
        """Initialize AsyncCursor with an active connection and cursor."""
        self._conn = conn
        self._cursor = cursor
        self.arraysize = 1

    async def execute(self, operation: str, parameters: Optional[Any] = None) -> "AsyncCursor":
        """
        Execute a database operation (query or command).

        Input: SQL operation string and optional parameters.
        Output: The cursor itself for chaining.
        Never: Modifies the operation string.
        """
        if parameters is None:
            await self._conn._execute_in_thread(self._cursor.execute, operation)
        else:
            await self._conn._execute_in_thread(self._cursor.execute, operation, parameters)
        return self

    async def executemany(self, operation: str, seq_of_parameters: Any) -> "AsyncCursor":
        """
        Execute a database operation against all parameter sequences.

        Input: SQL operation string and a sequence of parameters.
        Output: The cursor itself for chaining.
        Never: Interleaves operations with other threads.
        """
        await self._conn._execute_in_thread(self._cursor.executemany, operation, seq_of_parameters)
        return self

    async def fetchone(self) -> Optional[Any]:
        """
        Fetch the next row of a query result set.

        Input: None.
        Output: A single row or None.
        Never: Blocks the event loop.
        """
        return await self._conn._execute_in_thread(self._cursor.fetchone)

    async def fetchmany(self, size: Optional[int] = None) -> list:
        """
        Fetch the next set of rows of a query result.

        Input: Optional size for the number of rows to fetch.
        Output: A list of rows.
        Never: Returns more rows than the result set contains.
        """
        if size is None:
            size = self.arraysize
        return await self._conn._execute_in_thread(self._cursor.fetchmany, size)

    async def fetchall(self) -> list:
        """
        Fetch all remaining rows of a query result.

        Input: None.
        Output: A list of all remaining rows.
        Never: Leaves unfetched rows in the cursor result set.
        """
        return await self._conn._execute_in_thread(self._cursor.fetchall)

    async def close(self) -> None:
        """
        Close the cursor.

        Input: None.
        Output: None.
        Never: Closes the underlying connection.
        """
        await self._conn._execute_in_thread(self._cursor.close)

    @property
    def description(self) -> Optional[tuple]:
        """
        Return the cursor description.

        Input: None.
        Output: The underlying DuckDB cursor description or None.
        Never: Modifies the cursor state.
        """
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        """
        Return the rowcount of the last operation.

        Input: None.
        Output: The underlying cursor rowcount. Measurements show DuckDB reports -1 for SELECT, INSERT, UPDATE, DELETE, and executemany.
        Never: Computes the rowcount manually.
        """
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> None:
        """
        Return lastrowid.

        Input: None.
        Output: Always None, as DuckDB does not support standard row ID concepts on cursors.
        Never: Attempts to fetch a generated ID automatically.
        """
        return None


class AsyncConnection:
    """
    Awaitable connection for DuckDB.

    This wraps a DuckDB DB-API connection, managing its own single-worker
    thread to execute all operations serially without blocking the asyncio loop.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        """Initialize with an active DuckDB connection and create a worker thread."""
        self._conn = conn
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._in_transaction = False
        self._is_closed = False
        self.notices = []  # Exposed to make PGExecutionContext_psycopg2._log_notices happy.

    async def _execute_in_thread(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute a synchronous function in the executor thread.

        Input: A callable and its arguments.
        Output: The return value of the callable.
        Never: Blocks the asyncio event loop.
        """
        loop = asyncio.get_running_loop()

        def wrapper() -> T:
            return func(*args, **kwargs)

        return await loop.run_in_executor(self._executor, wrapper)

    async def cursor(self) -> AsyncCursor:
        """
        Return a new awaitable cursor.

        Input: None.
        Output: An AsyncCursor instance.
        Never: Returns a raw synchronous cursor.
        """
        c = await self._execute_in_thread(self._conn.cursor)
        return AsyncCursor(self, c)

    async def commit(self) -> None:
        """
        Commit the current transaction.

        Input: None.
        Output: None.
        Never: Swallows an unexpected error.
        """
        if self._in_transaction:
            await self._execute_in_thread(self._conn.commit)
            self._in_transaction = False

    async def rollback(self) -> None:
        """
        Roll back the current transaction.

        Input: None.
        Output: None.
        Never: Swallows an unexpected error.
        """
        if self._in_transaction:
            try:
                await self._execute_in_thread(self._conn.rollback)
            except duckdb.TransactionException as e:
                # DuckDB raises "TransactionContext Error: cannot rollback - no transaction is active"
                # when a transaction was already aborted/ended natively before we issue rollback.
                if "no transaction is active" not in str(e):
                    raise
            self._in_transaction = False

    async def close(self) -> None:
        """
        Close the connection and shut down the worker thread.

        Input: None.
        Output: None.
        Never: Raises an error if already closed.
        """
        if not self._is_closed:
            try:
                await self._execute_in_thread(self._conn.close)
            finally:
                self._executor.shutdown(wait=False)
                self._is_closed = True

    async def begin(self) -> None:
        """
        Begin a new transaction.

        Input: None.
        Output: None.
        Never: Starts a transaction if one is already active.
        """
        if not self._in_transaction:
            await self._execute_in_thread(self._conn.begin)
            self._in_transaction = True


async def connect(*args: Any, **kwargs: Any) -> AsyncConnection:
    """
    Open an awaitable duckdb connection.

    Input: Connection arguments for duckdb.connect.
    Output: An AsyncConnection instance.
    Never: Connects synchronously on the event loop.
    """
    loop = asyncio.get_running_loop()
    # duckdb.connect doesn't accept url_config, so pop it if it exists
    kwargs.pop("url_config", None)

    def _connect() -> duckdb.DuckDBPyConnection:
        return duckdb.connect(*args, **kwargs)

    conn = await loop.run_in_executor(None, _connect)
    return AsyncConnection(conn)
