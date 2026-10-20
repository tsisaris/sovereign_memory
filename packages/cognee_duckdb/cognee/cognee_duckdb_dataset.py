from uuid import UUID
from cognee.modules.users.models.DatasetDatabase import DatasetDatabase
from cognee.modules.users.models.User import User
from cognee.infrastructure.databases.dataset_database_handler.dataset_database_handler_interface import (
    DatasetDatabaseHandlerInterface,
)

class DuckDBDatasetDatabaseHandler(DatasetDatabaseHandlerInterface):
    @classmethod
    async def create_dataset(cls, dataset_id: UUID | None, user: User | None) -> dict:
        schema = f"ds_{dataset_id.hex}" if dataset_id else "public"
        return {
            "graph_dataset_database_handler": "duckdb_graph",
            "graph_database_connection_info": {"graph_database_schema": schema},
            "vector_dataset_database_handler": "duckdb_vector",
            "vector_database_connection_info": {"schema": schema},
        }

    @classmethod
    async def resolve_dataset_connection_info(
        cls, dataset_database: DatasetDatabase
    ) -> DatasetDatabase:
        return dataset_database

    @classmethod
    async def delete_dataset(cls, dataset_database: DatasetDatabase) -> None:
        pass
