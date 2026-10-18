"""Mem0 vector store on native DuckDB: FLOAT[n] vectors with exact cosine scan, JSON payload filters.

It can run on its own database file or on a borrowed native handle whose transactions its
caller controls. It never opens SQLite and never ignores a filter it cannot translate.
"""

import json
import logging
import re
import uuid
from typing import Any, ClassVar, Dict, List, Optional

import duckdb
from duckdb_sqlalchemy_async.native import connect_native, require_native_connection
from pydantic import BaseModel

from mem0.vector_stores.base import VectorStoreBase

logger = logging.getLogger(__name__)


class OutputData(BaseModel):
    """One stored vector's id, similarity score (None when not ranked) and payload."""

    id: str
    score: Optional[float]
    payload: Optional[dict]


class DuckDBVectorStore(VectorStoreBase):
    """A Mem0 collection stored as one DuckDB table of (id, vector, payload JSON)."""

    def __init__(
        self,
        db_path: str = "vector_store.duckdb",
        connection: Optional[duckdb.DuckDBPyConnection] = None,
        **kwargs,
    ):
        """Open the collection on a native DuckDB database.

        Args:
            db_path: Native DuckDB file (or ':memory:'); used only when no handle is supplied.
            connection: An optional caller-owned native handle. It is borrowed: this
                store never commits, rolls back or closes it, and its caller decides
                the transaction the store's writes belong to.
        """
        self.db_path = db_path
        self._owns_connection = connection is None
        if connection is not None:
            require_native_connection(connection)
            self.connection = connection
        else:
            self.connection = connect_native(db_path)
        self.collection_name = kwargs.get("collection_name", "mem0")
        self.embedding_model_dims = kwargs.get("embedding_model_dims", 1536)

        self._ensure_collection()

    def _ensure_collection(self):
        """Create the collection table if it does not exist; raises when creation fails."""
        try:
            self.connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.collection_name} (
                    id VARCHAR PRIMARY KEY,
                    vector FLOAT[{self.embedding_model_dims}],
                    payload JSON
                )
                """
            )
        except Exception as e:
            logger.error(f"Failed to create collection {self.collection_name}: {e}")
            raise

    def create_col(self, name: str, vector_size: int, distance: str = "cosine"):
        """Switch to the named collection and vector size, creating its table if needed."""
        self.collection_name = name
        self.embedding_model_dims = vector_size
        self._ensure_collection()

    def _in_txn(self) -> bool:
        """Say whether the handle is already inside a transaction its caller opened."""
        try:
            return (
                self.connection.execute("SELECT current_transaction_id()").fetchone()[0] is not None
            )
        except duckdb.CatalogException:
            return False

    def insert(
        self,
        vectors: List[List[float]],
        payloads: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        """Insert or replace vectors by id with their payloads, inside the caller's transaction if one is open."""
        if not vectors:
            return

        if payloads is None:
            payloads = [{} for _ in vectors]

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in vectors]

        if len(vectors) != len(payloads) or len(vectors) != len(ids):
            raise ValueError("Lengths of vectors, payloads, and ids must match")

        in_txn = self._in_txn()
        if not in_txn:
            self.connection.execute("BEGIN TRANSACTION")

        try:
            rows = []
            for i in range(len(vectors)):
                rows.append((ids[i], vectors[i], json.dumps(payloads[i])))

            self.connection.executemany(
                f"""
                INSERT INTO {self.collection_name} (id, vector, payload)
                VALUES (?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    vector = EXCLUDED.vector,
                    payload = EXCLUDED.payload
                """,
                rows,
            )

            if not in_txn:
                self.connection.execute("COMMIT")
        except Exception as e:
            if not in_txn:
                self.connection.execute("ROLLBACK")
            logger.error(f"Failed to insert vectors: {e}")
            raise

    _KEY: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    _COMPARISONS: ClassVar[Dict[str, str]] = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}

    @staticmethod
    def _text(value: Any) -> str:
        """Return a JSON scalar the way json_extract_string reports it."""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _field_condition(self, key: str, condition: Any) -> tuple[str, List[Any]]:
        """Translate one payload field condition into SQL and parameters.

        A bare value means equality, "*" means the field exists, and a dict maps
        operators (eq, ne, in, nin, gt, gte, lt, lte, contains, icontains) to
        values. An unknown field name or operator raises ValueError: a filter is
        never dropped, because dropping one widens the scope of a search.
        """
        if not self._KEY.fullmatch(key):
            raise ValueError(f"Unsupported filter field name: {key!r}")
        path = f"$.{key}"
        field = "json_extract_string(payload, ?)"
        if not isinstance(condition, dict):
            if condition == "*":
                return "json_exists(payload, ?)", [path]
            return f"{field} = ?", [path, self._text(condition)]
        clauses: List[str] = []
        params: List[Any] = []
        for op, val in condition.items():
            if op == "eq":
                clauses.append(f"{field} = ?")
                params.extend([path, self._text(val)])
            elif op == "ne":
                clauses.append(f"{field} IS DISTINCT FROM ?")
                params.extend([path, self._text(val)])
            elif op in ("in", "nin"):
                if not isinstance(val, (list, tuple, set)):
                    raise ValueError(f"Filter operator {op} needs a list")
                members = [self._text(v) for v in val]
                if not members:
                    clauses.append("FALSE" if op == "in" else "TRUE")
                    continue
                marks = ", ".join("?" for _ in members)
                if op == "in":
                    clauses.append(f"{field} IN ({marks})")
                else:
                    clauses.append(f"({field} IS NULL OR {field} NOT IN ({marks}))")
                    params.append(path)
                params.append(path)
                params.extend(members)
            elif op in self._COMPARISONS:
                symbol = self._COMPARISONS[op]
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    clauses.append(f"TRY_CAST({field} AS DOUBLE) {symbol} ?")
                else:
                    clauses.append(f"{field} {symbol} ?")
                params.extend([path, val if not isinstance(val, bool) else self._text(val)])
            elif op in ("contains", "icontains"):
                escaped = (
                    self._text(val).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                keyword = "LIKE" if op == "contains" else "ILIKE"
                clauses.append(f"{field} {keyword} ? ESCAPE '\\'")
                params.extend([path, f"%{escaped}%"])
            else:
                raise ValueError(f"Unsupported filter operator: {op}")
        return " AND ".join(clauses) if clauses else "TRUE", params

    def _filter_group(self, filters: Dict) -> tuple[str, List[Any]]:
        """Translate a filter dict (fields, "$or" and "$not" groups) into one SQL condition."""
        clauses: List[str] = []
        params: List[Any] = []
        for key, value in filters.items():
            if key == "$or":
                parts = [self._filter_group(branch) for branch in value]
                clauses.append("(" + " OR ".join(sql for sql, _ in parts) + ")")
                params.extend(p for _, branch in parts for p in branch)
            elif key == "$not":
                for branch in value:
                    sql, branch_params = self._filter_group(branch)
                    clauses.append(f"NOT ({sql})")
                    params.extend(branch_params)
            else:
                sql, field_params = self._field_condition(key, value)
                clauses.append(f"({sql})")
                params.extend(field_params)
        return " AND ".join(clauses) if clauses else "TRUE", params

    def _build_filter_clause(self, filters: Dict) -> tuple[str, List[Any]]:
        """Return ' AND <condition>' and its parameters for a search, or ('', []) without filters."""
        if not filters:
            return "", []
        sql, params = self._filter_group(filters)
        return f" AND {sql}", params

    def search(
        self, query: str, vectors: List[float], top_k: int = 5, filters: Optional[Dict] = None
    ) -> List[OutputData]:
        """Return the top_k nearest vectors by cosine similarity within the filters, best first."""
        where_clause, params = self._build_filter_clause(filters or {})

        query_sql = f"""
            SELECT id, 1.0 - array_cosine_distance(vector, CAST(? AS FLOAT[{self.embedding_model_dims}])) as score, payload
            FROM {self.collection_name}
            WHERE 1=1 {where_clause}
            ORDER BY score DESC
            LIMIT ?
        """
        all_params = [vectors] + params + [top_k]

        results = self.connection.execute(query_sql, all_params).fetchall()

        output = []
        for r in results:
            output.append(
                OutputData(id=r[0], score=r[1], payload=json.loads(r[2]) if r[2] else None)
            )
        return output

    def search_batch(
        self,
        queries: List[str],
        vectors_list: List[List[float]],
        top_k: int = 1,
        filters: Optional[Dict] = None,
    ) -> List[List[OutputData]]:
        """Run one search per query vector and return the result lists in the same order."""
        return [
            self.search(q, v, top_k=top_k, filters=filters)
            for q, v in zip(queries, vectors_list, strict=True)
        ]

    def keyword_search(
        self, query: str, top_k: int = 5, filters: Optional[Dict] = None
    ) -> List[OutputData]:
        """Return payload matches ranked by BM25 over a full-text index rebuilt from the current rows."""
        self.connection.execute(
            f"PRAGMA create_fts_index('{self.collection_name}', 'id', 'payload', overwrite=1)"
        )

        where_clause, params = self._build_filter_clause(filters or {})

        query_sql = f"""
            SELECT id, fts_main_{self.collection_name}.match_bm25(id, ?) as score, payload
            FROM {self.collection_name}
            WHERE fts_main_{self.collection_name}.match_bm25(id, ?) IS NOT NULL
            {where_clause}
            ORDER BY score DESC
            LIMIT ?
        """
        all_params = [query, query] + params + [top_k]

        results = self.connection.execute(query_sql, all_params).fetchall()

        output = []
        for r in results:
            output.append(
                OutputData(id=r[0], score=r[1], payload=json.loads(r[2]) if r[2] else None)
            )
        return output

    def delete(self, vector_id: str) -> None:
        """Delete one vector by id; deleting an absent id does nothing."""
        self.connection.execute(f"DELETE FROM {self.collection_name} WHERE id = ?", (vector_id,))

    def update(
        self, vector_id: str, vector: Optional[List[float]] = None, payload: Optional[Dict] = None
    ) -> None:
        """Replace a vector, a payload or both for an id; with neither, nothing changes."""
        if vector is None and payload is None:
            return

        if vector is not None and payload is not None:
            self.connection.execute(
                f"UPDATE {self.collection_name} SET vector = ?, payload = ? WHERE id = ?",
                (vector, json.dumps(payload), vector_id),
            )
        elif vector is not None:
            self.connection.execute(
                f"UPDATE {self.collection_name} SET vector = ? WHERE id = ?", (vector, vector_id)
            )
        elif payload is not None:
            self.connection.execute(
                f"UPDATE {self.collection_name} SET payload = ? WHERE id = ?",
                (json.dumps(payload), vector_id),
            )

    def get(self, vector_id: str) -> OutputData:
        """Return the stored vector's id and payload, or None when the id is absent."""
        result = self.connection.execute(
            f"SELECT id, NULL as score, payload FROM {self.collection_name} WHERE id = ?",
            (vector_id,),
        ).fetchone()

        if result:
            return OutputData(
                id=result[0], score=result[1], payload=json.loads(result[2]) if result[2] else None
            )
        return None

    def list_cols(self) -> List[str]:
        """Return the names of collection tables in the database, leaving out index and history tables."""
        tables = self.connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
        return [t[0] for t in tables if "fts" not in t[0] and t[0] not in ("history", "messages")]

    def delete_col(self) -> None:
        """Drop the collection table and its full-text index tables."""
        self.connection.execute(f"DROP TABLE IF EXISTS {self.collection_name}")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}_docs")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}_terms")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}_dict")

    def col_info(self) -> Dict:
        """Return the collection's name, row count and vector dimensions."""
        count = self.connection.execute(f"SELECT COUNT(*) FROM {self.collection_name}").fetchone()[
            0
        ]
        return {
            "name": self.collection_name,
            "count": count,
            "embedding_model_dims": self.embedding_model_dims,
        }

    def list(self, filters: Optional[Dict] = None, top_k: int = 100) -> List[OutputData]:
        """Return up to top_k stored vectors within the filters, in no guaranteed order."""
        where_clause, params = self._build_filter_clause(filters or {})

        query_sql = f"""
            SELECT id, NULL as score, payload
            FROM {self.collection_name}
            WHERE 1=1 {where_clause}
            LIMIT ?
        """
        all_params = params + [top_k]

        results = self.connection.execute(query_sql, all_params).fetchall()

        output = []
        for r in results:
            output.append(
                OutputData(id=r[0], score=r[1], payload=json.loads(r[2]) if r[2] else None)
            )
        return output

    def reset(self) -> None:
        """Drop and recreate the empty collection."""
        self.delete_col()
        self._ensure_collection()

    def __del__(self):
        """Close the handle this store opened itself; never one it borrowed."""
        try:
            if getattr(self, "_owns_connection", False) and self.connection:
                self.connection.close()
        except duckdb.Error:
            logger.debug("DuckDB handle was already closed", exc_info=True)
