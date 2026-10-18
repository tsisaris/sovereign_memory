"""Register DuckDB adapters."""

from cognee.infrastructure.databases.graph import use_graph_adapter
from cognee.infrastructure.databases.vector import use_vector_adapter

from .duckdb_adapter import DuckDBAdapter

use_vector_adapter("duckdb", DuckDBAdapter)
use_graph_adapter("duckdb", DuckDBAdapter)


class DuckDBDatasetHandler:
    """DuckDB Dataset Handler."""

    def get_database_name(self, dataset_name: str) -> str:
        """Get database name."""
        return dataset_name


# Add compatibility
try:
    from cognee.infrastructure.databases.dataset_database_handler import (
        supported_dataset_database_handlers,
    )

    supported_dataset_database_handlers["duckdb"] = {
        "handler_instance": DuckDBDatasetHandler,
        "handler_provider": ("duckdb",),
    }
except ImportError:
    pass
