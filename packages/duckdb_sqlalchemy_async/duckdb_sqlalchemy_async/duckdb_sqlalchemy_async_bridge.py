"""Run DuckDB operations on one transaction handle per asynchronous connection.

Logical cursors retain buffered results and metadata. They never create a second
native connection or close the transaction owner's handle. Streaming is not
supported: each execute snapshots its complete result before another job runs.
"""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, TypeVar

import duckdb

from .native import connect_native

T = TypeVar("T")
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
    """Hold one logical cursor's results without owning a native connection."""

    def __init__(self, conn: "AsyncConnection"):
        """Create an empty result cursor for the supplied transaction owner."""
        self._conn = conn
        self._rows: deque = deque()
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self.arraysize = 1
        self._closed = False

    def _check_open(self) -> None:
        """Reject operations after logical cursor or native connection closure."""
        if self._closed or self._conn.closed:
            raise InterfaceError("Cursor or connection is closed")

    def _execute(self, operation: str, parameters: Any, many: bool) -> None:
        """Execute and snapshot results on the owning worker in one job."""
        self._check_open()
        self._rows.clear()
        self.description = None
        self.rowcount = -1
        native = self._conn.native
        if many:
            native.executemany(operation, parameters)
        elif parameters is None:
            native.execute(operation)
        else:
            native.execute(operation, parameters)
        self.description = native.description
        self.rowcount = native.rowcount
        if self.description:
            self._rows.extend(native.fetchall())

    async def execute(self, operation: str, parameters: Any = None) -> "AsyncCursor":
        """Execute SQL and retain its rows and metadata before returning."""
        await self._conn.run(self._execute, operation, parameters, False)
        return self

    async def executemany(self, operation: str, seq_of_parameters: Any) -> "AsyncCursor":
        """Execute parameter sets on the connection's existing transaction."""
        await self._conn.run(self._execute, operation, seq_of_parameters, True)
        return self

    async def fetchone(self) -> Any:
        """Return the next buffered row, or None when exhausted."""
        self._check_open()
        return self._rows.popleft() if self._rows else None

    async def fetchmany(self, size: int | None = None) -> list:
        """Return up to size buffered rows, defaulting to arraysize."""
        self._check_open()
        count = self.arraysize if size is None else size
        return [self._rows.popleft() for _ in range(min(count, len(self._rows)))]

    async def fetchall(self) -> list:
        """Consume all rows belonging to this logical cursor."""
        self._check_open()
        rows = list(self._rows)
        self._rows.clear()
        return rows

    async def close(self) -> None:
        """Release buffered rows; never close the transaction owner's handle."""
        self._rows.clear()
        self._closed = True


async def _drain(future: asyncio.Future) -> None:
    """Wait through repeated cancellation until a retained worker job finishes."""
    while not future.done():
        try:
            await asyncio.wait({future})
        except asyncio.CancelledError:
            continue
    if not future.cancelled():
        future.exception()


class AsyncConnection:
    """Serialize every operation and transaction on one dedicated worker."""

    def __init__(self):
        """Create a worker and gate without opening a database on the event loop."""
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._gate = asyncio.Lock()
        self._native: duckdb.DuckDBPyConnection | None = None
        self._in_transaction = False
        self.closed = False
        self.notices: list = []

    @property
    def native(self) -> duckdb.DuckDBPyConnection:
        """Return the worker-owned handle or reject access before opening."""
        if self._native is None:
            raise InterfaceError("Connection has not opened")
        return self._native

    async def run(self, func: Callable[..., T], *args: Any) -> T:
        """Serialize a worker job; cancellation drains and invalidates its handle.

        Interrupt targets only the current job while the connection gate is held.
        A canceled transaction is never returned to a pool for further use.
        """
        async with self._gate:
            if self.closed:
                raise InterfaceError("Connection is closed")
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, partial(func, *args))
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                if self._native is not None and not future.done():
                    self._native.interrupt()
                await _drain(future)
                cleanup = loop.run_in_executor(self._executor, self._close)
                await _drain(cleanup)
                self._executor.shutdown(wait=False)
                raise

    def _open(self, args: tuple, kwargs: dict) -> None:
        """Open the database on the same worker that owns subsequent operations."""
        self._native = connect_native(*args, **kwargs)

    async def cursor(self) -> AsyncCursor:
        """Create a logical cursor without opening another native connection."""
        if self.closed:
            raise InterfaceError("Connection is closed")
        return AsyncCursor(self)

    def _begin(self) -> None:
        """Begin once and update state inside the worker's completed operation."""
        if not self._in_transaction:
            self.native.begin()
            self._in_transaction = True

    async def begin(self) -> None:
        """Begin a transaction under the same gate used by SQL operations."""
        await self.run(self._begin)

    def _finish(self, commit: bool) -> None:
        """Finish an active transaction and retain state on any unexpected error."""
        if self._in_transaction:
            if commit:
                self.native.commit()
            else:
                self.native.rollback()
            self._in_transaction = False

    async def commit(self) -> None:
        """Commit the transaction on the SQL execution handle."""
        await self.run(self._finish, True)

    async def rollback(self) -> None:
        """Roll back the transaction on the SQL execution handle."""
        if not self.closed:
            await self.run(self._finish, False)

    def _close(self) -> None:
        """Close the native handle on its worker, discarding any transaction."""
        try:
            if self._native is not None:
                self._native.close()
        finally:
            self.closed = True
            self._in_transaction = False

    async def close(self) -> None:
        """Close idempotently, draining any previous operation before shutdown."""
        async with self._gate:
            if self.closed:
                return
            future = asyncio.get_running_loop().run_in_executor(self._executor, self._close)
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                await _drain(future)
                raise
            finally:
                self._executor.shutdown(wait=False)


async def connect(*args: Any, **kwargs: Any) -> AsyncConnection:
    """Open a worker-owned connection, honoring DuckDB URL configuration."""
    config = dict(kwargs.pop("config", {}))
    config.update(kwargs.pop("url_config", {}))
    kwargs["config"] = config
    connection = AsyncConnection()
    try:
        await connection.run(connection._open, args, kwargs)
    except BaseException:
        await connection.close()
        raise
    return connection
