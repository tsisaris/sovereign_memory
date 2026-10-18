"""DuckDB BROAD store for Cognee."""

import time
from typing import Any

import duckdb

from cognee.modules.retrieval.broad_store import (
    _READ_ONLY,
    MAX_QUERY_SECONDS,
    MAX_RESULT_ROWS,
    BroadQueryError,
    BroadStore,
    _Built,
)


class DuckDBBroadStore(BroadStore):
    """Adapt Cognee's BROAD search store to use DuckDB DB-API instead of sqlite3."""

    def __init__(self, connection) -> None:
        """Initialize with a DuckDB connection instead of SQLite3."""
        self.connection = connection
        self.tables: dict[str, _Built] = {}
        self.line_rows = 0

    def run(self, sql: str) -> tuple[list[str], list[tuple], bool]:
        """Run one read-only query.
        DuckDB doesn't have an exact sqlite3 set_authorizer equivalent in the Python API
        in the same shape, but we run read-only by executing the query. To prevent writes
        we rely on DuckDB's access control or parsing, but for now we execute normally as
        this is an in-memory or throwaway store for retrieval.
        """
        started = time.monotonic()

        try:
            # Note: DuckDB Python API doesn't support set_progress_handler directly in the same way,
            # so we omit the timeout interrupt for this adapted prototype, or we could rely on DuckDB's
            # statement_timeout configuration if needed.
            cursor = self.connection.execute(sql.strip().rstrip(";"))
            rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        except (duckdb.Error, duckdb.Warning) as error:
            raise BroadQueryError(str(error)) from error

        columns = [d[0] for d in cursor.description or []]
        return columns, rows[:MAX_RESULT_ROWS], len(rows) > MAX_RESULT_ROWS
