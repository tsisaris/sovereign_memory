"""Expose the maintained DuckDB BROAD store through the adapter's existing import."""

from cognee.modules.retrieval.broad_store import RecordStore as DuckDBBroadStore

__all__ = ["DuckDBBroadStore"]
