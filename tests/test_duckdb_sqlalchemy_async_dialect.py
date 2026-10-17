"""Tests for the duckdb asyncio dialect."""

import asyncio
import time
from multiprocessing import Process

import duckdb
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine


def writer(filename, ready_event):
    """Hold the duckdb file open to simulate a writer process."""
    conn = duckdb.connect(str(filename), read_only=False)
    ready_event.set()
    time.sleep(10)
    conn.close()


@pytest.mark.asyncio
async def test_duckdb_sqlalchemy_async_dialect(tmp_path):
    """Test the async dialect behaves correctly across all required states."""
    existing_file = tmp_path / "existing.duckdb"
    conn = duckdb.connect(str(existing_file))
    conn.execute("CREATE TABLE t (x INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.close()

    held_file = tmp_path / "held.duckdb"
    from multiprocessing import Manager

    manager = Manager()
    ready_event = manager.Event()

    p = Process(target=writer, args=(held_file, ready_event))
    p.start()
    ready_event.wait(5)

    cases = [
        {
            "desc": "in-memory URL",
            "url": "duckdb+aio:///:memory:",
            "action": lambda conn: conn.execute(text("SELECT 1")),
            "expected_success": True,
            "expected_result_func": lambda res: res.scalar() == 1,
            "expected_error": None,
        },
        {
            "desc": "file URL to a new file",
            "url": f"duckdb+aio:///{tmp_path}/new.duckdb",
            "action": lambda conn: conn.execute(text("SELECT 1")),
            "expected_success": True,
            "expected_result_func": lambda res: res.scalar() == 1,
            "expected_error": None,
        },
        {
            "desc": "file URL to an existing file",
            "url": f"duckdb+aio:///{existing_file}",
            "action": lambda conn: conn.execute(text("SELECT x FROM t")),
            "expected_success": True,
            "expected_result_func": lambda res: res.scalar() == 1,
            "expected_error": None,
        },
        {
            "desc": "file URL to an unreadable path",
            "url": f"duckdb+aio:////root/unreadable.duckdb",
            "action": lambda conn: conn.execute(text("SELECT 1")),
            "expected_success": False,
            "expected_result_func": None,
            "expected_error": DatabaseError,
        },
        {
            "desc": "one-writer rule: held read-write by another process",
            "url": f"duckdb+aio:///{held_file}",
            "action": lambda conn: conn.execute(text("SELECT 1")),
            "expected_success": False,
            "expected_result_func": None,
            "expected_error": DatabaseError,
        },
        {
            "desc": "? placeholders",
            "url": "duckdb+aio:///:memory:",
            "action": _run_qmark,
            "expected_success": True,
            "expected_result_func": lambda res: res == 42,
            "expected_error": None,
        },
        {
            "desc": "$name placeholders",
            "url": "duckdb+aio:///:memory:",
            "action": lambda conn: conn.execute(text("SELECT :val"), {"val": 43}),
            "expected_success": True,
            "expected_result_func": lambda res: res.scalar() == 43,
            "expected_error": None,
        },
        {
            "desc": "executemany",
            "url": "duckdb+aio:///:memory:",
            "action": _run_executemany,
            "expected_success": True,
            "expected_result_func": lambda res: res == 3,
            "expected_error": None,
        },
        {
            "desc": "a rolled-back transaction",
            "url": "duckdb+aio:///:memory:",
            "action": _run_rollback,
            "expected_success": True,
            "expected_result_func": lambda res: res == 0,
            "expected_error": None,
        },
        {
            "desc": "a statement returning zero rows",
            "url": "duckdb+aio:///:memory:",
            "action": lambda conn: conn.execute(text("SELECT 1 WHERE 1=0")),
            "expected_success": True,
            "expected_result_func": lambda res: res.fetchone() is None,
            "expected_error": None,
        },
        {
            "desc": "a statement with a syntax error",
            "url": "duckdb+aio:///:memory:",
            "action": lambda conn: conn.execute(text("SELCT 1")),
            "expected_success": False,
            "expected_result_func": None,
            "expected_error": (DatabaseError, ProgrammingError),
        },
        {
            "desc": "closing a closed connection",
            "url": "duckdb+aio:///:memory:",
            "action": _run_close_closed,
            "expected_success": True,
            "expected_result_func": lambda res: res is True,
            "expected_error": None,
        },
    ]

    for row in cases:
        engine = create_async_engine(row["url"])
        try:
            async with engine.connect() as conn:
                if asyncio.iscoroutinefunction(row["action"]):
                    res = await row["action"](conn)
                else:
                    res = await row["action"](conn)

            assert row["expected_success"] is True, (
                f"{row['desc']} failed: Expected failure, but succeeded."
            )
            if row["expected_result_func"]:
                assert row["expected_result_func"](res), (
                    f"{row['desc']} failed: Result func check failed."
                )
        except Exception as e:
            if not row["expected_success"]:
                if isinstance(e, AssertionError):
                    raise e
                assert isinstance(e, row["expected_error"]), (
                    f"{row['desc']} failed: Got {type(e)} expected {row['expected_error']}"
                )
            else:
                pytest.fail(f"{row['desc']} failed with unexpected exception: {e}")
        finally:
            await engine.dispose()

    p.terminate()
    p.join()


async def _run_qmark(conn):
    """Run a query using standard qmark positional placeholders natively."""

    def do_sync_query(sync_conn):
        c = sync_conn.connection._connection._conn.cursor()
        c.execute("SELECT ?", [42])
        return c.fetchone()[0]

    return await conn.run_sync(do_sync_query)


async def _run_executemany(conn):
    """Run an executemany insert and return the inserted count."""
    await conn.execute(text("CREATE TABLE em (x INT)"))
    await conn.execute(text("INSERT INTO em VALUES (:x)"), [{"x": 1}, {"x": 2}, {"x": 3}])
    res = await conn.execute(text("SELECT count(*) FROM em"))
    return res.scalar()


async def _run_rollback(conn):
    """Test rolling back a transaction."""
    await conn.execute(text("CREATE TABLE rb (x INT)"))

    def do_sync_rollback(sync_conn):
        raw = sync_conn.connection._connection._conn
        # duckdb's native rollback behavior when not explicitly in a transaction ignores it.
        # But we can force it. We should not use BEGIN TRANSACTION if sqlalchemy already started it.
        # Let's just do an insert, then sqlalchemy rollback.
        raw.execute("INSERT INTO rb VALUES (1)")
        # we do a raw rollback to test bridge rollback logic works.
        try:
            raw.rollback()
        except duckdb.TransactionException:
            pass
        c = raw.cursor()
        c.execute("SELECT count(*) FROM rb")
        return c.fetchone()[0]

    return await conn.run_sync(do_sync_rollback)


async def _run_close_closed(conn):
    """Test closing an already closed connection doesn't fail."""

    def do_sync_close(sync_conn):
        sync_conn.connection.close()
        sync_conn.connection.close()
        return True

    return await conn.run_sync(do_sync_close)
