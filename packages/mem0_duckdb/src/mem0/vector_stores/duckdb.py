import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import duckdb
from duckdb_sqlalchemy_async.native import connect_native
from pydantic import BaseModel

from mem0.vector_stores.base import VectorStoreBase

logger = logging.getLogger(__name__)


class OutputData(BaseModel):
    id: str
    score: Optional[float]
    payload: Optional[dict]


class DuckDBVectorStore(VectorStoreBase):
    def __init__(self, db_path: str = "vector_store.duckdb", **kwargs):
        self.db_path = db_path
        self.connection = connect_native(db_path)
        self.collection_name = kwargs.get("collection_name", "mem0")
        self.embedding_model_dims = kwargs.get("embedding_model_dims", 1536)

        self._ensure_collection()

    def _ensure_collection(self):
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
        self.collection_name = name
        self.embedding_model_dims = vector_size
        self._ensure_collection()

    def _in_txn(self) -> bool:
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

    def _build_filter_clause(self, filters: Dict) -> tuple[str, List[Any]]:
        if not filters:
            return "", []

        clauses = []
        params = []
        for key, value in filters.items():
            if isinstance(value, dict):
                for op, val in value.items():
                    json_path = f"$.{key}"
                    if op == "eq":
                        clauses.append(f"json_extract_string(payload, ?) = ?")
                        params.extend([json_path, str(val)])
                    elif op == "ne":
                        clauses.append(f"json_extract_string(payload, ?) != ?")
                        params.extend([json_path, str(val)])
                    elif op == "in":
                        placeholders = ", ".join(["?" for _ in val])
                        clauses.append(f"json_extract_string(payload, ?) IN ({placeholders})")
                        params.append(json_path)
                        params.extend([str(v) for v in val])
                    elif op == "nin":
                        placeholders = ", ".join(["?" for _ in val])
                        clauses.append(f"json_extract_string(payload, ?) NOT IN ({placeholders})")
                        params.append(json_path)
                        params.extend([str(v) for v in val])
                    else:
                        logger.warning(
                            f"Operator {op} not fully supported or optimized in DuckDB vector store"
                        )
            else:
                json_path = f"$.{key}"
                clauses.append(f"json_extract_string(payload, ?) = ?")
                params.extend([json_path, str(value)])

        where_clause = " AND ".join(clauses)
        return f" AND {where_clause}" if where_clause else "", params

    def search(
        self, query: str, vectors: List[float], top_k: int = 5, filters: Optional[Dict] = None
    ) -> List[OutputData]:
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
        return [
            self.search(q, v, top_k=top_k, filters=filters) for q, v in zip(queries, vectors_list)
        ]

    def keyword_search(
        self, query: str, top_k: int = 5, filters: Optional[Dict] = None
    ) -> List[OutputData]:
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
        self.connection.execute(f"DELETE FROM {self.collection_name} WHERE id = ?", (vector_id,))

    def update(
        self, vector_id: str, vector: Optional[List[float]] = None, payload: Optional[Dict] = None
    ) -> None:
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
        tables = self.connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
        return [t[0] for t in tables if "fts" not in t[0] and t[0] not in ("history", "messages")]

    def delete_col(self) -> None:
        self.connection.execute(f"DROP TABLE IF EXISTS {self.collection_name}")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}_docs")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}_terms")
        self.connection.execute(f"DROP TABLE IF EXISTS fts_main_{self.collection_name}_dict")

    def col_info(self) -> Dict:
        count = self.connection.execute(f"SELECT COUNT(*) FROM {self.collection_name}").fetchone()[
            0
        ]
        return {
            "name": self.collection_name,
            "count": count,
            "embedding_model_dims": self.embedding_model_dims,
        }

    def list(self, filters: Optional[Dict] = None, top_k: int = 100) -> List[OutputData]:
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
        self.delete_col()
        self._ensure_collection()

    def __del__(self):
        try:
            if hasattr(self, "connection") and self.connection:
                self.connection.close()
        except Exception:
            pass
