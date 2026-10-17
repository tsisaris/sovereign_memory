"""Tests for the duckdb asyncio dialect."""

import asyncio
import time
from multiprocessing import get_context

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
    context = get_context("spawn")
    ready_event = context.Event()
    p = context.Process(target=writer, args=(held_file, ready_event))
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0)
    p.start()

    try:
        assert ready_event.wait(5), "Writer did not acquire its DuckDB file"
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
                "url": f"duckdb+aio:///{unreadable}/test.duckdb",
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
                "action": lambda conn: conn.exec_driver_sql("SELECT $val", {"val": 43}),
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
                "desc": "commits and independent connection transactions",
                "url": f"duckdb+aio:///{tmp_path}/independent.duckdb",
                "action": _run_independent,
                "expected_success": True,
                "expected_result_func": lambda res: res == [1, 1, 2],
                "expected_error": None,
            },
            {
                "desc": "independent buffered results and metadata",
                "url": "duckdb+aio:///:memory:",
                "action": _run_results,
                "expected_success": True,
                "expected_result_func": lambda res: (
                    res == (["first"], [(1,), (2,)], ["second"], [(3,)])
                ),
                "expected_error": None,
            },
            {
                "desc": "cancel an active insert and reuse the engine safely",
                "url": f"duckdb+aio:///{tmp_path}/cancel.duckdb",
                "action": _run_cancel,
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

    finally:
        unreadable.chmod(0o700)
        if p.is_alive():
            p.terminate()
        p.join()
        p.close()


async def _run_qmark(conn):
    """Exercise positional binding through the public SQLAlchemy connection."""
    result = await conn.exec_driver_sql("SELECT ?", (42,))
    return result.scalar()


async def _run_executemany(conn):
    """Run an executemany insert and return the inserted count."""
    await conn.execute(text("CREATE TABLE em (x INT)"))
    await conn.execute(text("INSERT INTO em VALUES (:x)"), [{"x": 1}, {"x": 2}, {"x": 3}])
    res = await conn.execute(text("SELECT count(*) FROM em"))
    return res.scalar()


async def _run_rollback(conn):
    """Commit schema, roll back an insert, and query through SQLAlchemy."""
    await conn.execute(text("CREATE TABLE rb (x INT)"))
    await conn.commit()
    await conn.execute(text("INSERT INTO rb VALUES (1)"))
    await conn.rollback()
    result = await conn.execute(text("SELECT count(*) FROM rb"))
    return result.scalar()


async def _run_close_closed(conn):
    """Close the public SQLAlchemy connection twice without an error."""
    await conn.close()
    await conn.close()
    return conn.closed


async def _run_independent(conn):
    """Prove commits persist and one connection's rollback leaves another intact."""
    await conn.exec_driver_sql("CREATE TABLE independent (x INT)")
    await conn.exec_driver_sql("INSERT INTO independent VALUES (1)")
    await conn.commit()
    async with conn.engine.connect() as other:
        observed = [(await other.exec_driver_sql("SELECT count(*) FROM independent")).scalar()]
        await other.rollback()
        await conn.exec_driver_sql("INSERT INTO independent VALUES (2)")
        observed.append((await other.exec_driver_sql("SELECT count(*) FROM independent")).scalar())
        await other.rollback()
        await other.exec_driver_sql("INSERT INTO independent VALUES (3)")
        await other.commit()
        await conn.rollback()
        observed.append((await other.exec_driver_sql("SELECT count(*) FROM independent")).scalar())
    return observed


async def _run_results(conn):
    """Keep earlier rows and column names after another statement executes."""
    first = await conn.exec_driver_sql("SELECT i AS first FROM range(1, 3) t(i)")
    second = await conn.exec_driver_sql("SELECT 3 AS second")
    return list(first.keys()), first.fetchall(), list(second.keys()), second.fetchall()


async def _run_cancel(conn):
    """Cancel unfinished database work and prove the next checkout sees no writes."""
    await conn.exec_driver_sql("CREATE TABLE canceled (x BIGINT)")
    await conn.commit()
    task = asyncio.create_task(
        conn.exec_driver_sql("INSERT INTO canceled SELECT i FROM range(1000000000) t(i)")
    )
    await asyncio.sleep(0.05)
    assert not task.done(), "Cancellation must exercise unfinished work"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await conn.rollback()
    async with conn.engine.connect() as other:
        return (await other.exec_driver_sql("SELECT count(*) FROM canceled")).scalar()
