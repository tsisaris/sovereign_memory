"""Checks for the DuckDB relational adapter: creating tables and running queries, and keeping separate schemas apart.

Each check uses its own temporary database file.
"""

import asyncio

import pytest
from sqlalchemy import text

# Need to run tests through our own adapted files, but they rely on being imported properly.
# We will test the adapters directly.


@pytest.fixture(scope="session")
def event_loop():
    """Test fixture: give each session its own new event loop and close it afterwards.

    Comes out: the loop.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tmp_db(tmp_path):
    """Test fixture: give the DuckDB connection URL of a database file inside pytest's temporary directory.

    Comes out: the URL string. It creates no file itself.
    """
    return f"duckdb+aio:///{tmp_path}/test.duckdb"


@pytest.mark.asyncio
async def test_relational_adapter_duckdb(tmp_path):
    """Check that the DuckDB relational adapter creates its database and tables and answers a query, on a temporary file."""
    from cognee.cognee_duckdb_relational import DuckDBRelationalAdapter

    db_file = tmp_path / "test.duckdb"
    adapter = DuckDBRelationalAdapter(f"duckdb+aio:///{db_file}")

    await adapter.create_database()

    # Test table creation
    await adapter.create_table(
        "main",
        "test_table",
        [{"name": "id", "type": "INTEGER", "primary_key": True}, {"name": "val", "type": "STRING"}],
    )

    # Check it exists
    tables = await adapter.get_table_names()
    assert "test_table" in tables

    # Check schema list
    schemas = await adapter.get_schema_list()
    assert isinstance(schemas, list)

    await adapter.delete_database()
    assert not db_file.exists()


@pytest.mark.asyncio
async def test_schema_isolation_duckdb(tmp_path):
    """Check that data written to two DuckDB schemas stays separate, on a temporary file."""
    from cognee.cognee_duckdb_relational import DuckDBRelationalAdapter

    db_file = tmp_path / "iso.duckdb"
    adapter = DuckDBRelationalAdapter(f"duckdb+aio:///{db_file}")

    async with adapter.engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA ds_1"))
        await conn.execute(text("CREATE SCHEMA ds_2"))

        await conn.execute(text("CREATE TABLE ds_1.items (id INT)"))
        await conn.execute(text("CREATE TABLE ds_2.items (id INT)"))

        await conn.execute(text("INSERT INTO ds_1.items VALUES (1)"))
        await conn.execute(text("INSERT INTO ds_2.items VALUES (2)"))

        res1 = await conn.execute(text("SELECT * FROM ds_1.items"))
        assert res1.scalar() == 1

        res2 = await conn.execute(text("SELECT * FROM ds_2.items"))
        assert res2.scalar() == 2

    schemas = await adapter.get_schema_list()
    assert "ds_1" in schemas
    assert "ds_2" in schemas

    await adapter.engine.dispose()
