"""Implement Cognee's dataset-handler contract with isolated schemas in one file."""

import asyncio
from pathlib import Path
from uuid import UUID

from duckdb_engine import Dialect
from duckdb_sqlalchemy_async.native import connect_native

from cognee.infrastructure.databases.relational.config import get_relational_config
from cognee.modules.users.models.DatasetDatabase import DatasetDatabase
from cognee.modules.users.models.User import User


def _dataset_location(dataset_id: UUID | None) -> tuple[Path, str]:
    """Derive the configured DuckDB file and a schema from a real dataset UUID."""
    if dataset_id is None:
        raise ValueError("Isolated DuckDB storage requires a dataset UUID")
    schema = f"ds_{UUID(str(dataset_id)).hex}"
    config = get_relational_config()
    if config.db_provider != "duckdb" or config.db_name == ":memory:":
        raise ValueError("DuckDB datasets require the configured local DuckDB file")
    return (Path(config.db_path) / config.db_name).resolve(), schema


def _create_schema(database: Path, schema: str) -> None:
    """Create only the named namespace and close its native provisioning handle."""
    database.parent.mkdir(parents=True, exist_ok=True)
    identifier = Dialect().identifier_preparer.quote_identifier(schema)
    with connect_native(str(database)) as connection:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {identifier}")


async def _prepare_dataset(dataset_id: UUID | None) -> tuple[str, str]:
    """Provision a dataset namespace without opening another database file."""
    database, schema = _dataset_location(dataset_id)
    await asyncio.to_thread(_create_schema, database, schema)
    return str(database), schema


def _drop_schema(database: Path, schema: str) -> None:
    """Drop one namespace if its file exists; never remove or recreate the file."""
    if not database.exists():
        return
    if not database.is_file():
        raise ValueError("The configured DuckDB database is not a file")
    identifier = Dialect().identifier_preparer.quote_identifier(schema)
    with connect_native(str(database)) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {identifier} CASCADE")


async def _delete_dataset(record: DatasetDatabase, database_url: str, schema: str) -> None:
    """Evict this dataset's handles and delete only its verified namespace."""
    database, expected_schema = _dataset_location(record.dataset_id)
    if schema != expected_schema or not database_url or Path(database_url).resolve() != database:
        raise ValueError("Dataset storage does not match the configured DuckDB namespace")
    from cognee.infrastructure.databases.graph.get_graph_engine import graph_engine_cache
    from cognee.infrastructure.databases.vector.create_vector_engine import vector_engine_cache

    await graph_engine_cache.aevict_for_database(schema)
    await vector_engine_cache.aevict_for_database(schema)
    await asyncio.to_thread(_drop_schema, database, schema)


class DuckDBGraphDatasetDatabaseHandler:
    """Provide actual graph coordinates through Cognee's maintained handler protocol."""

    @classmethod
    async def create_dataset(cls, dataset_id: UUID | None, user: User | None) -> dict:
        """Create graph storage and return only graph fields for the dataset record."""
        database, schema = await _prepare_dataset(dataset_id)
        return {
            "graph_database_provider": "duckdb",
            "graph_database_url": database,
            "graph_database_name": schema,
            "graph_database_key": "",
            "graph_dataset_database_handler": "duckdb_graph",
            "graph_database_connection_info": {},
        }

    @classmethod
    async def resolve_dataset_connection_info(
        cls, dataset_database: DatasetDatabase
    ) -> DatasetDatabase:
        """Return local coordinates unchanged without resolving or persisting credentials."""
        return dataset_database

    @classmethod
    async def delete_dataset(cls, dataset_database: DatasetDatabase) -> None:
        """Delete this graph namespace while preserving the file and other datasets."""
        await _delete_dataset(
            dataset_database,
            dataset_database.graph_database_url,
            dataset_database.graph_database_name,
        )


class DuckDBVectorDatasetDatabaseHandler:
    """Provide vector coordinates without duplicating graph fields or creating files."""

    @classmethod
    async def create_dataset(cls, dataset_id: UUID | None, user: User | None) -> dict:
        """Create vector storage and return only vector fields for the dataset record."""
        database, schema = await _prepare_dataset(dataset_id)
        return {
            "vector_database_provider": "duckdb",
            "vector_database_url": database,
            "vector_database_name": schema,
            "vector_database_key": "",
            "vector_dataset_database_handler": "duckdb_vector",
            "vector_database_connection_info": {},
        }

    @classmethod
    async def resolve_dataset_connection_info(
        cls, dataset_database: DatasetDatabase
    ) -> DatasetDatabase:
        """Return the same local coordinates without altering graph fields or credentials."""
        return dataset_database

    @classmethod
    async def delete_dataset(cls, dataset_database: DatasetDatabase) -> None:
        """Delete this vector namespace without unlinking the shared database file."""
        await _delete_dataset(
            dataset_database,
            dataset_database.vector_database_url,
            dataset_database.vector_database_name,
        )
