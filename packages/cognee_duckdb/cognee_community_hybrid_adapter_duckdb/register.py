"""Register the actual DuckDB adapter without replacing dataset handlers with stubs."""

from cognee.infrastructure.databases.graph import use_graph_adapter
from cognee.infrastructure.databases.vector import use_vector_adapter

from .duckdb_adapter import DuckDBAdapter

use_vector_adapter("duckdb", DuckDBAdapter)
use_graph_adapter("duckdb", DuckDBAdapter)
