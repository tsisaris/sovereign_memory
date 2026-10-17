"""
Asynchronous bridge for DuckDB's Python DB-API.

This module provides awaitable Connection and Cursor objects over duckdb's DB-API,
executing each call in a worker thread per connection, mimicking the aiosqlite pattern.
It wraps synchronous DB-API calls and yields execution to the asyncio event loop.
"""

import asyncio
from typing import Any, Callable, Optional, TypeVar

import duckdb

T = TypeVar("T")

# Map duckdb exceptions to DB-API 2.0 standard exception names
Warning = duckdb.Warning
Error = duckdb.Error
InterfaceError = duckdb.Error  # DuckDB doesn't have InterfaceError, map to Error
DatabaseError = duckdb.Error
DataError = duckdb.DataError
OperationalError = duckdb.OperationalError
IntegrityError = duckdb.IntegrityError
InternalError = duckdb.InternalError
ProgrammingError = duckdb.ProgrammingError
NotSupportedError = duckdb.NotSupportedError

paramstyle = "qmark"


class AsyncCursor:
    """Awaitable cursor for DuckDB."""

    def __init__(self, conn: "AsyncConnection", cursor: duckdb.DuckDBPyConnection):
        """Initialize AsyncCursor with an active connection and thread executor."""
        self._conn = conn
        self._cursor = cursor

    async def _execute_in_thread(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await self._conn._execute_in_thread(func, *args, **kwargs)

    async def execute(self, operation: str, parameters: Optional[Any] = None) -> "AsyncCursor":
        """Execute a database operation (query or command)."""
        if parameters is None:
            await self._execute_in_thread(self._cursor.execute, operation)
        else:
            await self._execute_in_thread(self._cursor.execute, operation, parameters)
        return self

    async def executemany(self, operation: str, seq_of_parameters: Any) -> "AsyncCursor":
        """Execute a database operation against all parameter sequences."""
        await self._execute_in_thread(self._cursor.executemany, operation, seq_of_parameters)
        return self

    async def fetchone(self) -> Optional[Any]:
        """Fetch the next row of a query result set."""
        return await self._execute_in_thread(self._cursor.fetchone)

    async def fetchmany(self, size: Optional[int] = None) -> list:
        """Fetch the next set of rows of a query result."""
        if size is None:
            size = 1
        return await self._execute_in_thread(self._cursor.fetchmany, size)

    async def fetchall(self) -> list:
        """Fetch all remaining rows of a query result."""
        return await self._execute_in_thread(self._cursor.fetchall)

    async def close(self) -> None:
        """Close the cursor."""
        await self._execute_in_thread(self._cursor.close)

    @property
    def description(self) -> Optional[tuple]:
        """Return the cursor description."""
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        """Return the rowcount of the last operation."""
        return -1

    @property
    def lastrowid(self) -> None:
        """Return lastrowid, which DuckDB does not support. Always None."""
        return None

    def __getattr__(self, name: str) -> Any:
        """Forward missing attributes to duckdb connection."""
        return getattr(self._cursor, name)


class AsyncConnection:
    """Awaitable connection for DuckDB."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        """Initialize with an active duckdb connection."""
        self._conn = conn
        self._loop = asyncio.get_event_loop()

    async def _execute_in_thread(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a synchronous function in the executor thread."""

        def wrapper() -> T:
            return func(*args, **kwargs)

        return await self._loop.run_in_executor(None, wrapper)

    async def cursor(self) -> AsyncCursor:
        """Return a new awaitable cursor."""
        c = await self._execute_in_thread(self._conn.cursor)
        return AsyncCursor(self, c)

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self._execute_in_thread(self._conn.commit)

    async def rollback(self) -> None:
        """Roll back the current transaction."""
        try:
            await self._execute_in_thread(self._conn.rollback)
        except duckdb.TransactionException:
            pass  # ignore "cannot rollback - no transaction is active"

    async def close(self) -> None:
        """Close the connection."""
        await self._execute_in_thread(self._conn.close)

    async def begin(self) -> None:
        """Begin a new transaction."""
        await self._execute_in_thread(self._conn.execute, "BEGIN")

    def __getattr__(self, name: str) -> Any:
        """Forward missing attributes to duckdb connection."""
        return getattr(self._conn, name)


async def connect(*args: Any, **kwargs: Any) -> AsyncConnection:
    """Open an awaitable duckdb connection."""
    loop = asyncio.get_event_loop()
    # duckdb.connect doesn't accept url_config, so pop it if it exists
    kwargs.pop("url_config", None)

    def _connect() -> duckdb.DuckDBPyConnection:
        return duckdb.connect(*args, **kwargs)

    conn = await loop.run_in_executor(None, _connect)
    return AsyncConnection(conn)
