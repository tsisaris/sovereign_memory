"""
Alembic hooks for the async DuckDB dialect.
"""

from alembic.ddl.impl import DefaultImpl


class DuckDBImpl(DefaultImpl):
    """Alembic implementation class for DuckDB."""

    __dialect__ = "duckdb"
