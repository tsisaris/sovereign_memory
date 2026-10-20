"""Checks that the DuckDB relational adapter keeps data in separate schemas apart, the DuckDB form of the PostgreSQL schema-isolation test.

Each check uses a temporary database file.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

# Testing schema isolation specifically on the DuckDB relational adapter
# The postgres e2e test adapted for DuckDB behavior since DuckDB supports schemas.


@pytest.fixture(scope="session")
def event_loop():
    """Test fixture: give each session its own new event loop and close it afterwards.

    Comes out: the loop.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_duckdb_schema_isolation(tmp_path):
    """Check that rows written under two schemas stay isolated and can be read back separately, on a temporary DuckDB file."""
    from cognee.cognee_duckdb_relational import DuckDBRelationalAdapter

    db_file = tmp_path / "test_schemas.duckdb"
    adapter = DuckDBRelationalAdapter(f"duckdb+aio:///{db_file}")

    s1 = "ds_1"
    s2 = "ds_2"

    # Setup schemas
    async with adapter.engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {s1}"))
        await conn.execute(text(f"CREATE SCHEMA {s2}"))

        await conn.execute(text(f"CREATE TABLE {s1}.Entity_name (id UUID, text VARCHAR)"))
        await conn.execute(text(f"CREATE TABLE {s2}.Entity_name (id UUID, text VARCHAR)"))

        await conn.execute(text(f"INSERT INTO {s1}.Entity_name VALUES ('{uuid4()}', 'alice')"))
        await conn.execute(text(f"INSERT INTO {s2}.Entity_name VALUES ('{uuid4()}', 'bob')"))

        res1 = await conn.execute(text(f"SELECT * FROM {s1}.Entity_name"))
        rows1 = res1.fetchall()
        assert len(rows1) == 1
        assert rows1[0][1] == "alice"

        res2 = await conn.execute(text(f"SELECT * FROM {s2}.Entity_name"))
        rows2 = res2.fetchall()
        assert len(rows2) == 1
        assert rows2[0][1] == "bob"

    schemas = await adapter.get_schema_list()
    assert s1 in schemas
    assert s2 in schemas

    extracted = await adapter.extract_schema()
    assert f"{s1}.Entity_name" in extracted
    assert f"{s2}.Entity_name" in extracted

    await adapter.engine.dispose()
