"""Preserve the legacy storage import name while using DuckDB exclusively.

The alias accepts DuckDB files, not pre-existing SQLite database files. It does
not silently convert or overwrite a different database format.
"""

from mem0.memory.duckdb_storage import DuckDBManager

SQLiteManager = DuckDBManager

__all__ = ["DuckDBManager", "SQLiteManager"]
