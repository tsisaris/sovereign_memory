import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Need to run tests through our own adapted files, but they rely on being imported properly.
# We will test the adapters directly.


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tmp_db(tmp_path):
    return f"duckdb+aio:///{tmp_path}/test.duckdb"


@pytest.mark.asyncio
async def test_relational_adapter_duckdb(tmp_path):
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
