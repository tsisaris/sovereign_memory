"""Register DuckDB adapters."""

from cognee.infrastructure.databases.graph import use_graph_adapter
from cognee.infrastructure.databases.vector import use_vector_adapter

from .duckdb_adapter import DuckDBAdapter

use_vector_adapter("duckdb", DuckDBAdapter)
use_graph_adapter("duckdb", DuckDBAdapter)

from cognee.infrastructure.databases.dataset_database_handler import use_dataset_database_handler


class DuckDBDatasetHandler:
    """DuckDB Dataset Handler."""

    def get_database_name(self, dataset_name: str) -> str:
        """Get database name."""
        return dataset_name


use_dataset_database_handler("duckdb", DuckDBDatasetHandler, "duckdb")
