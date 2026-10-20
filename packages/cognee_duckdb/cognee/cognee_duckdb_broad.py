import duckdb
import time
from typing import Any
from cognee.modules.retrieval.broad_store import BroadStore, BroadQueryError, _Built, MAX_RESULT_ROWS, MAX_QUERY_SECONDS, _READ_ONLY

class DuckDBBroadStore(BroadStore):
    def __init__(self, connection) -> None:
        self.connection = connection
        self.tables: dict[str, _Built] = {}
        self.line_rows = 0

    def run(self, sql: str) -> tuple[list[str], list[tuple], bool]:
        started = time.monotonic()
        try:
            cursor = self.connection.execute(sql.strip().rstrip(";"))
            rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        except (duckdb.Error, duckdb.Warning) as error:
            raise BroadQueryError(str(error)) from error
        
        columns = [d[0] for d in cursor.description or []]
        return columns, rows[:MAX_RESULT_ROWS], len(rows) > MAX_RESULT_ROWS
