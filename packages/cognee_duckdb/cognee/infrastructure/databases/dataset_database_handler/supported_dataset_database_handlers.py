"""Register the DuckDB storage handlers supported by this DuckDB-only fork."""

from cognee.cognee_duckdb_dataset import (
    DuckDBGraphDatasetDatabaseHandler,
    DuckDBVectorDatasetDatabaseHandler,
)

supported_dataset_database_handlers = {
    "duckdb_graph": {
        "handler_instance": DuckDBGraphDatasetDatabaseHandler,
        "handler_provider": "duckdb",
    },
    "duckdb_vector": {
        "handler_instance": DuckDBVectorDatasetDatabaseHandler,
        "handler_provider": "duckdb",
    },
}
