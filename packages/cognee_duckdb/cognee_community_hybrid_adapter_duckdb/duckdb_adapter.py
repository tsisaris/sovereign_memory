"""DuckDB Vector and Graph adapter package."""

from typing import ClassVar

"""DuckDB Vector and Graph adapter package."""
import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, List, Optional, TypeVar
from uuid import UUID

import duckdb
from cognee.infrastructure.databases.graph.graph_db_interface import EdgeData, GraphDBInterface
from cognee.infrastructure.databases.graph.graph_topology import (
    _component_sizes,
    _select_nodeset_neighbor_ids,
)
from cognee.infrastructure.databases.vector.embeddings.EmbeddingEngine import (
    EmbeddingEngine,
)
from cognee.infrastructure.databases.vector.exceptions import (
    CollectionNotFoundError as CogneeCollectionNotFoundError,
)
from cognee.infrastructure.databases.vector.models.ScoredResult import ScoredResult
from cognee.infrastructure.databases.vector.vector_db_interface import VectorDBInterface
from cognee.infrastructure.engine import DataPoint
from cognee.shared.logging_utils import get_logger
from duckdb_engine import Dialect
from duckdb_sqlalchemy_async.native import connect_native, require_native_connection

T = TypeVar("T")


class CollectionNotFoundError(CogneeCollectionNotFoundError):
    """Collection-not-found error.

    Subclasses cognee's CollectionNotFoundError so core retrieval code
    (which catches the cognee exception to treat missing collections as
    empty results) handles it correctly.
    """

    pass


class DuckDBDataPoint(DataPoint):  # type: ignore[misc]
    """DuckDB data point schema for vector index entries.

    Attributes:
        text: The text content to be indexed.
        metadata: Metadata containing index field configuration.
    """

    text: str
    metadata: ClassVar[dict[str, Any]] = {"index_fields": ["text"]}


def serialize_for_json(obj: Any) -> Any:
    """Convert objects to JSON-serializable format.

    Args:
        obj: Object to serialize (UUID, dict, list, or any other type).

    Returns:
        JSON-serializable representation of the object.
    """
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_for_json(item) for item in obj]
    else:
        return obj


logger = get_logger("DuckDBAdapter")


class DuckDBAdapter(VectorDBInterface, GraphDBInterface):
    """DuckDB hybrid adapter implementing both vector and graph database interfaces."""

    name = "DuckDB"
    supports_payload_update = True

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        database_name: str | None = "cognee",
        embedding_engine: EmbeddingEngine | None = None,
        graph_database_username: str | None = None,
        graph_database_password: str | None = None,
        graph_database_url: str | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
        **kwargs,
    ) -> None:
        """Open (or borrow) the DuckDB handle that holds Cognee's graph and vector tables.

        Without ``connection`` the adapter owns its handle: it opens ``url`` (or memory)
        natively, creates the ``database_name`` schema, selects it, runs each write
        batch in its own transaction and closes the handle in ``close``.

        With ``connection`` the adapter borrows a native handle the caller opened. It
        checks the handle is native DuckDB storage and issues nothing else at
        construction; afterwards it never sets the schema, opens another connection or
        cursor, begins, commits, rolls back or closes: every statement runs on the
        caller's handle, inside whatever transaction the caller holds, against tables
        qualified with ``database_name``. Passing ``url`` or ``graph_database_url``
        together with ``connection`` raises ValueError; a handle that is not a DuckDB
        connection raises TypeError.
        """
        if connection is not None:
            if url or graph_database_url:
                raise ValueError("Pass either a DuckDB file location or a borrowed connection")
            if not isinstance(connection, duckdb.DuckDBPyConnection):
                raise TypeError("A borrowed connection must be a native DuckDB connection")
            require_native_connection(connection)
        if url and graph_database_url and url != graph_database_url:
            raise ValueError("Conflicting DuckDB file locations")
        url = url or graph_database_url
        if url and url != ":memory:":
            if "://" in url or url.startswith("md:"):
                raise ValueError("This adapter requires a local DuckDB file path")
            database = Path(url).expanduser().resolve()
            database.parent.mkdir(parents=True, exist_ok=True)
            url = str(database)
        self.database_url = url
        self.api_key = api_key
        self.database_name = database_name or "cognee"
        self.embedding_engine = embedding_engine
        self.graph_database_username = graph_database_username
        self.graph_database_password = graph_database_password
        self.VECTOR_DB_LOCK = asyncio.Lock()
        self._schema = self._identifier(self.database_name)
        self._node_table = f"{self._schema}.graph_node"
        self._edge_table = f"{self._schema}.graph_edge"
        self._metadata_table = f"{self._schema}.graph_metadata"
        self.owns_connection = connection is None

        if connection is not None:
            self.connection = connection
            return

        # Owned handle: the file at database_url, or a private in-memory database.
        if url:
            self.connection = connect_native(url)
        else:
            self.connection = connect_native()

        try:
            self.connection.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
            self.connection.execute("SET schema = ?", [self.database_name])
        except BaseException:
            self.connection.close()
            raise

    @staticmethod
    def _identifier(name: str) -> str:
        """Quote one identifier so names cannot select another schema or add SQL."""
        return Dialect().identifier_preparer.quote_identifier(name)

    def _table(self, name: str) -> str:
        """Return the quoted, schema-qualified name of one vector collection table."""
        return f"{self._schema}.{self._identifier(name)}"

    async def _execute_query(self, query: str, params: list[Any] | None = None) -> Any:
        """Execute a query on the DuckDB connection with async lock."""
        async with self.VECTOR_DB_LOCK:
            if params:
                return self.connection.execute(query, params).fetchall()
            else:
                return self.connection.execute(query).fetchall()

    async def _execute_query_one(self, query: str, params: list[Any] | None = None) -> Any:
        """Execute a query and return one result with async lock."""
        async with self.VECTOR_DB_LOCK:
            if params:
                return self.connection.execute(query, params).fetchone()
            else:
                return self.connection.execute(query).fetchone()

    def _run_in_transaction(self, work: Callable[[], T]) -> T:
        """Run work on the handle; an owned handle wraps it in its own transaction.

        An owned handle begins, commits and, on any failure of work or commit, rolls
        back and raises the original error; a BEGIN that fails (a transaction is
        already open) rolls back nothing, since this method acquired nothing. A
        borrowed handle runs work inside the caller's transaction and leaves
        commit or rollback to the caller; errors propagate unchanged. The caller
        holds the adapter lock.
        """
        if not self.owns_connection:
            return work()
        self.connection.execute("BEGIN TRANSACTION")
        try:
            result = work()
            self.connection.execute("COMMIT")
            return result
        except BaseException as error:
            # The transaction is ours (BEGIN succeeded), so every unsuccessful path ends it.
            try:
                self.connection.execute("ROLLBACK")
            except duckdb.TransactionException as rollback_failure:
                # The database already ended it; the original error is the one to report.
                raise error from rollback_failure
            raise

    async def _execute_transaction(self, queries: list[tuple[str, list[Any] | None]]) -> None:
        """Run a batch of write statements in order under the adapter lock.

        See ``_run_in_transaction`` for how an owned and a borrowed handle differ.
        """

        def work() -> None:
            """Execute every statement of the batch on the handle."""
            for query, params in queries:
                if params:
                    self.connection.execute(query, params)
                else:
                    self.connection.execute(query)

        async with self.VECTOR_DB_LOCK:
            self._run_in_transaction(work)

    async def close(self) -> None:
        """Close an owned DuckDB handle; a borrowed handle is left open for its caller."""
        async with self.VECTOR_DB_LOCK:
            if self.owns_connection and hasattr(self, "connection"):
                self.connection.close()

    # VectorDBInterface methods
    async def embed_data(self, data: list[str]) -> list[list[float]]:
        """[VECTOR] Embed text data using the embedding engine."""
        if not self.embedding_engine:
            raise ValueError("Embedding engine not configured")
        result = await self.embedding_engine.embed_text(data)
        return result

    async def has_collection(self, collection_name: str) -> bool:
        """[VECTOR] Check if a collection exists."""
        try:
            # Check if table exists in DuckDB
            result = await self._execute_query_one(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = current_database() AND table_schema = $1 "
                "AND table_name = $2",
                [self.database_name, collection_name],
            )
            return result is not None
        except duckdb.Error:
            return False

    async def create_collection(
        self, collection_name: str, vector_dimension: int | None = None
    ) -> None:
        """[VECTOR] Create a new collection (table) in DuckDB."""
        # Create a table for storing vector data with specified dimension
        if vector_dimension is None:
            if self.embedding_engine is None:
                raise ValueError("An explicit vector dimension or embedding engine is required")
            vector_dimension = self.embedding_engine.get_vector_size()
        if not isinstance(vector_dimension, int) or vector_dimension <= 0:
            raise ValueError("Vector dimension must be a positive integer")
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {self._table(collection_name)} (
            id VARCHAR PRIMARY KEY,
            text TEXT,
            vector FLOAT[{vector_dimension}],
            payload JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        await self._execute_transaction(
            [(f"CREATE SCHEMA IF NOT EXISTS {self._schema}", None), (create_table_query, None)]
        )

    async def create_data_points(self, collection_name: str, data_points: list[DataPoint]) -> None:
        """[VECTOR] Create data points in the collection."""
        if not await self.has_collection(collection_name):
            raise CollectionNotFoundError(f"Collection {collection_name} not found!")

        data_vectors = await self.embed_data(
            [DataPoint.get_embeddable_data(data_point) for data_point in data_points]
        )

        # Create the data points (use INSERT OR REPLACE to handle duplicates)
        create_data_points_query = f"""
        INSERT OR REPLACE INTO {self._table(collection_name)} (id, text, vector, payload) VALUES ($1, $2, $3, $4)
        """
        await self._execute_transaction(
            [
                (
                    create_data_points_query,
                    [
                        str(data_point.id),
                        DataPoint.get_embeddable_data(data_point),
                        data_vectors[i],
                        json.dumps(serialize_for_json(data_point.model_dump())),
                    ],
                )
                for i, data_point in enumerate(data_points)
            ]
        )

    async def upsert_raw_vectors(
        self,
        collection_name: str,
        points: list[dict],
        payload_schema: Any | None = None,
    ) -> None:
        """[VECTOR] Write caller-computed vectors without calling the embedding engine.

        Each point is a dict with ``id``, ``vector`` (a non-empty list of floats, the
        same length for every point) and an optional ``payload`` dict. When
        ``payload_schema`` (a Pydantic model class) is given, each payload is validated
        and normalised by it. A missing collection is created with the vectors'
        dimension; existing ids are replaced. A missing id, a non-list or empty vector
        or mixed lengths raise ValueError before anything is written. No points does
        nothing. Embeddings are never computed here, so a caller can embed first and
        then write inside its own transaction.
        """
        if not points:
            return
        rows = []
        dimension = None
        for point in points:
            point_id = point.get("id")
            vector = point.get("vector")
            payload = point.get("payload") or {}
            if point_id is None:
                raise ValueError("Raw vector point is missing id")
            if not isinstance(vector, list) or not vector:
                raise ValueError("Raw vector point vector must be a non-empty list")
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise ValueError(
                    f"Raw vector size {len(vector)} does not match expected size {dimension}"
                )
            if payload_schema is not None:
                payload = payload_schema.model_validate(payload).model_dump()
            rows.append([str(point_id), None, vector, json.dumps(serialize_for_json(payload))])

        await self.create_collection(collection_name, vector_dimension=dimension)
        upsert_query = (
            f"INSERT OR REPLACE INTO {self._table(collection_name)} "
            "(id, text, vector, payload) VALUES ($1, $2, $3, $4)"
        )
        await self._execute_transaction([(upsert_query, row) for row in rows])

    async def create_vector_index(self, index_name: str, index_property_name: str) -> None:
        """[VECTOR] Create a vector index for a specific property."""
        await self.create_collection(f"{index_name}_{index_property_name}")

    async def index_data_points(
        self, index_name: str, index_property_name: str, data_points: list[DataPoint]
    ) -> None:
        """[VECTOR] Index data points in the collection."""
        await self.create_data_points(
            f"{index_name}_{index_property_name}",
            [
                DuckDBDataPoint(
                    id=data_point.id,
                    text=getattr(data_point, data_point.metadata.get("index_fields", ["text"])[0]),
                )
                for data_point in data_points
            ],
        )

    async def retrieve(
        self, collection_name: str, data_point_ids: list[str]
    ) -> list[dict[str, Any]]:
        """[VECTOR] Retrieve data points by their IDs."""
        try:
            if not await self.has_collection(collection_name):
                logger.warning(
                    f"Collection '{collection_name}' not found in DuckDBAdapter.retrieve; "
                    f"returning []."
                )
                return []

            results = []

            for data_id in data_point_ids:
                # Query DuckDB for the specific data point
                query = f"SELECT payload FROM {self._table(collection_name)} WHERE id = $1"
                result = await self._execute_query_one(query, [data_id])

                if result:
                    # Parse the stored payload JSON
                    payload_str = result[0] if isinstance(result, list | tuple) else result
                    try:
                        payload = json.loads(payload_str)
                        results.append(payload)
                    except (json.JSONDecodeError, TypeError):
                        # Fallback if payload parsing fails
                        logger.warning(f"Failed to parse payload for data point {data_id}")
                        results.append({"id": data_id, "error": "Failed to parse payload"})

            return results

        except duckdb.Error as e:
            logger.error(f"Error retrieving data points: {str(e)}")
            return []

    async def search(
        self,
        collection_name: str,
        query_text: str | None = None,
        query_vector: list[float] | None = None,
        limit: int | None = 15,
        with_vector: bool = True,
        include_payload: bool = False,
        node_name: Optional[List[str]] = None,
        node_name_filter_operator: str = "OR",
    ) -> list[ScoredResult]:
        """[VECTOR] Search for similar vectors."""
        from cognee.infrastructure.databases.exceptions import (
            MissingQueryParameterError,
        )

        """[VECTOR] Search for similar vectors.

        Args:
            collection_name: Name of the collection to search in.
            query_text: Text to search for (will be embedded if provided).
            query_vector: Pre-computed query vector for search.
            limit: Maximum number of results to return. Set to 0 to return all rows.
            with_vector: Whether to include vectors in the results.

        Returns:
            List of scored results ordered by ascending cosine distance (nearest match first).
        """

        from cognee.infrastructure.engine.utils import parse_id

        if query_text is None and query_vector is None:
            raise MissingQueryParameterError()

        if not await self.has_collection(collection_name):
            logger.warning(
                f"Collection '{collection_name}' not found in DuckDBAdapter.search; returning []."
            )
            return []

        if limit is None:
            search_query = f"""select count(*) from {self._table(collection_name)}"""
            count = await self._execute_query_one(search_query)
            if count is None:
                logger.warning("Count is None in DuckDBAdapter.search; returning [].")
                return []
            limit = count[0]

        if limit <= 0:
            logger.warning("Limit is 0 or less in DuckDBAdapter.search; returning [].")
            return []

        try:
            # Get the query vector
            if query_vector is None and query_text is not None:
                query_vector = (await self.embed_data([query_text]))[0]

            # Ensure we have a query vector at this point
            if query_vector is None:
                raise MissingQueryParameterError()

            # Use DuckDB's native array_distance function for efficient vector search
            # Convert query vector to DuckDB array format with proper dimension
            vector_dimension = self.embedding_engine.get_vector_size()
            vector_str = f"[{','.join(map(str, query_vector))}]::FLOAT[{vector_dimension}]"

            # Execute vector similarity search using cosine similarity

            select_fields = [
                "id",
                f"array_cosine_distance(vector, {vector_str}) as distance",
            ]
            if with_vector:
                select_fields.append("vector")
            if include_payload:
                select_fields.append("payload")

            # Optionally filter on the "belongs_to_set" payload field by node_name.
            # belongs_to_set is stored inside the JSON payload as a list of names.
            where_clause = ""
            query_params: list[Any] = []
            if node_name:
                # Extract belongs_to_set from the JSON payload as a list of strings.
                belongs_expr = "CAST(json_extract_string(payload, '$.belongs_to_set') AS VARCHAR[])"
                if node_name_filter_operator == "AND":
                    list_fn = "list_has_all"
                else:
                    list_fn = "list_has_any"
                where_clause = f"WHERE {list_fn}({belongs_expr}, $2)"
                query_params.append(node_name)

            query_params.append(limit)

            search_query = f"""
            SELECT {", ".join(select_fields)}
            FROM {self._table(collection_name)}
            {where_clause}
            ORDER BY distance ASC
            LIMIT ${len(query_params)}
            """

            search_results = await self._execute_query(
                search_query, query_params if query_params else None
            )

            if not search_results:
                return []

            # Convert results to ScoredResult objects
            results = []
            for row in search_results:
                distance = row[1]  # distance is the 2nd column (index 1)

                # ScoredResult.score is the raw backend distance (lower is better), matching the
                # built-in cognee adapters (LanceDB, PGVector). Do not invert it into a similarity.
                score = distance
                # Parse the payload JSON
                payload = {}
                if include_payload:
                    payload = row[3] if with_vector else row[2]
                payload_data = json.loads(payload) if payload else {}

                result = ScoredResult(
                    id=parse_id(row[0]),
                    score=score,
                    payload=payload_data,
                )
                results.append(result)

            return results

        except duckdb.Error as e:
            logger.error(f"Error during search: {str(e)}")
            raise e

    async def batch_search(
        self,
        collection_name: str,
        query_texts: list[str],
        limit: int | None = 15,
        with_vectors: bool = False,
        include_payload: bool = False,
        node_name: Optional[List[str]] = None,
    ) -> list[list[ScoredResult]]:
        """[VECTOR] Perform batch vector search."""
        # Embed all queries at once
        vectors = await self.embed_data(query_texts)

        # Execute searches in parallel
        search_tasks = [
            self.search(
                collection_name=collection_name,
                query_text=None,
                query_vector=vector,
                limit=limit,
                with_vector=with_vectors,
                include_payload=include_payload,
                node_name=node_name,
            )
            for vector in vectors
        ]

        results = await asyncio.gather(*search_tasks)

        # Return all results (consistent with individual search method behavior)
        return results

    async def delete_data_points(
        self, collection_name: str, data_point_ids: list[UUID]
    ) -> dict[str, int]:
        """[VECTOR] Delete data points by their ids (UUIDs or their text form) and count them."""
        try:
            if not await self.has_collection(collection_name):
                logger.warning(
                    f"Collection '{collection_name}' not found in DuckDBAdapter.delete_data_points"
                )
                return {"deleted": 0}

            if not data_point_ids:
                return {"deleted": 0}

            # Create placeholders for the IN clause
            placeholders = ", ".join([f"${i + 1}" for i in range(len(data_point_ids))])
            delete_query = (
                f"DELETE FROM {self._table(collection_name)} "
                f"WHERE id IN ({placeholders}) RETURNING id"
            )

            # Execute the deletion
            deleted_rows = await self._execute_query(
                delete_query, [str(data_point_id) for data_point_id in data_point_ids]
            )
            deleted_count = len(deleted_rows)

            logger.info(f"Deleted {deleted_count} data points from collection {collection_name}")
            return {"deleted": deleted_count}

        except duckdb.Error as e:
            logger.error(f"Error deleting data points: {str(e)}")
            raise e

    async def score_by_ids(
        self, collection_name: str, data_point_ids: list[str], query_vector: list[float]
    ) -> list[ScoredResult]:
        """[VECTOR] Return raw cosine distances (lower is nearer) for the requested ids only.

        Only rows whose id is requested are read and scored, with no top-k cut-off;
        missing ids are omitted and the order of the result is unspecified. No ids
        returns []. A missing collection raises CollectionNotFoundError and a query
        vector of another dimension raises DuckDB's error. It never writes.
        """
        ids = list(dict.fromkeys(str(point_id) for point_id in data_point_ids))
        if not ids:
            return []
        if not await self.has_collection(collection_name):
            raise CollectionNotFoundError(f"Collection {collection_name} not found!")
        if not query_vector:
            raise ValueError("A query vector must have at least one dimension")
        from cognee.infrastructure.engine.utils import parse_id

        rows = await self._execute_query(
            f"SELECT id, array_cosine_distance(vector, CAST($1 AS FLOAT[{len(query_vector)}])) "
            f"FROM {self._table(collection_name)} WHERE list_contains($2, id)",
            [query_vector, ids],
        )
        return [
            ScoredResult(id=parse_id(str(row[0])), score=float(row[1]), payload=None)
            for row in rows
        ]

    async def update_payload(
        self, collection_name: str, payload_updates: dict[str, dict[str, Any]]
    ) -> None:
        """[VECTOR] Overwrite payload fields of existing rows; stored vectors are never touched.

        Missing ids and a missing collection are skipped. Every named field must
        already exist in that row's payload (Cognee's caller contract); if any does
        not, ValueError is raised before anything is written. The read and the
        write happen under one adapter lock, so no concurrent writer is overwritten.
        """
        if not payload_updates or not await self.has_collection(collection_name):
            return
        updates = {str(point_id): fields for point_id, fields in payload_updates.items()}
        table = self._table(collection_name)

        def work() -> None:
            """Read the current payloads, validate every update, then write them."""
            rows = self.connection.execute(
                f"SELECT id, payload FROM {table} WHERE list_contains($1, id)", [list(updates)]
            ).fetchall()
            merged = {}
            for row_id, payload_text in rows:
                payload = json.loads(payload_text) if payload_text else {}
                unknown = sorted(set(updates[row_id]) - set(payload))
                if unknown:
                    raise ValueError(
                        f"update_payload: fields {unknown} do not exist in the payload of "
                        f"{collection_name!r} row {row_id}"
                    )
                merged[row_id] = {**payload, **updates[row_id]}
            for row_id, payload in merged.items():
                self.connection.execute(
                    f"UPDATE {table} SET payload = $1 WHERE id = $2",
                    [json.dumps(serialize_for_json(payload)), row_id],
                )

        async with self.VECTOR_DB_LOCK:
            self._run_in_transaction(work)

    @classmethod
    async def create_dataset(cls, dataset_id: UUID | None, user: Any) -> dict:
        """[DATASET] Return the vector connection fields for a dataset's DuckDB namespace.

        This is Cognee's own DuckDB dataset handler: the dataset gets its own schema
        (``ds_<uuid hex>``) in the configured DuckDB file, derived from the relational
        configuration, so no path, credential or registry is invented here. Raises
        ValueError without a dataset id or without a configured local DuckDB file.
        """
        from cognee.cognee_duckdb_dataset import DuckDBVectorDatasetDatabaseHandler

        return await DuckDBVectorDatasetDatabaseHandler.create_dataset(dataset_id, user)

    async def delete_dataset(self, dataset_id: UUID, user: Any) -> None:
        """[DATASET] Drop one dataset's schema from this handle's file, for the dataset's owner only.

        The dataset's record in Cognee's relational metadata is read on this
        adapter's own handle, so it sees the caller's uncommitted changes and
        opens no other engine, file or connection. The schema is dropped only when
        the record belongs to ``user``, is a DuckDB record, names the schema
        derived from the dataset id and names the file this handle works in; the
        check and the drop are one step inside one transaction (owned) or inside
        the caller's (borrowed, left to the caller to finish). Other schemas, the
        file and the handle's current schema are untouched. A dataset with no
        record is already gone and nothing happens. Raises ValueError when this
        file holds no Cognee relational metadata or the record does not match,
        and PermissionError for another user's dataset. Cached engines of the
        dropped schema are evicted afterwards.
        """
        from cognee.infrastructure.databases.graph.get_graph_engine import graph_engine_cache
        from cognee.infrastructure.databases.vector.create_vector_engine import vector_engine_cache
        from cognee.modules.users.models import DatasetDatabase
        from duckdb_sqlalchemy_async.native import borrowed_engine
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        schema = f"ds_{UUID(str(dataset_id)).hex}"

        def work() -> bool:
            """Check the record and drop the schema on this handle; True when dropped."""
            metadata = self.connection.execute(
                "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
                "AND schema_name = current_schema() AND table_name = $1",
                [DatasetDatabase.__tablename__],
            ).fetchone()
            if not metadata or metadata[0] != 1:
                raise ValueError("This DuckDB file holds no Cognee dataset metadata to delete from")
            engine = borrowed_engine(self.connection)
            try:
                with Session(engine) as session:
                    record = session.scalar(
                        select(DatasetDatabase).where(DatasetDatabase.dataset_id == dataset_id)
                    )
            finally:
                engine.dispose()
            if record is None:
                return False
            if record.owner_id != user.id:
                raise PermissionError("Only the owner of a dataset may delete its storage")
            file_rows = self.connection.execute(
                "SELECT path FROM duckdb_databases() WHERE database_name = current_database()"
            ).fetchall()
            stored = record.vector_database_url
            if (
                record.vector_database_provider != "duckdb"
                or record.vector_database_name != schema
                or not stored
                or not file_rows
                or not file_rows[0][0]
                or Path(stored).resolve() != Path(file_rows[0][0]).resolve()
            ):
                raise ValueError("Dataset storage does not match this adapter's DuckDB file")
            self.connection.execute(f"DROP SCHEMA IF EXISTS {self._identifier(schema)} CASCADE")
            return True

        async with self.VECTOR_DB_LOCK:
            dropped = self._run_in_transaction(work)
        if dropped:
            await graph_engine_cache.aevict_for_database(schema)
            await vector_engine_cache.aevict_for_database(schema)

    async def prune(self) -> None:
        """Documentation."""
        try:
            q = (
                "SELECT table_name FROM information_schema.columns "
                "WHERE column_name = 'vector' AND table_catalog = current_database() "
                "AND table_schema = $1"
            )
            res = await self._execute_query(q, [self.database_name])
            tables = [r[0] for r in res]
            for table in tables:
                await self._execute_query(f"DROP TABLE IF EXISTS {self._table(table)}")
            logger.info("Pruned all DuckDB vector collections")
        except duckdb.Error as e:
            logger.error(f"Error during prune: {str(e)}")
            raise e

    async def get_collection_names(self):
        """
        Get names of all collections in the database.

        Returns:
            List of collection names. In this case of Redis, the return type is a dict.
        """
        collection_names = []
        # Get all table names from the database
        tables_query = (
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_catalog = current_database() AND table_schema = $1 "
            "AND column_name = 'vector'"
        )
        tables_result = await self._execute_query(tables_query, [self.database_name])
        for row in tables_result:
            table_name = row[0] if isinstance(row, list | tuple) else row
            collection_names.append(table_name)

        return collection_names

    # GraphDBInterface methods

    # GraphDBInterface methods

    async def initialize(self) -> None:
        """[GRAPH] Initialize the graph database schema."""
        create_node_table = f"""
        CREATE TABLE IF NOT EXISTS {self._node_table} (
            id VARCHAR PRIMARY KEY,
            name VARCHAR,
            type VARCHAR,
            properties JSON,
            source_ref_keys VARCHAR[],
            source_dataset_ids VARCHAR[],
            source_run_ids VARCHAR[],
            source_run_refs VARCHAR[]
        )
        """
        create_edge_table = f"""
        CREATE TABLE IF NOT EXISTS {self._edge_table} (
            source_id VARCHAR,
            target_id VARCHAR,
            relationship_name VARCHAR,
            properties JSON,
            source_ref_keys VARCHAR[],
            source_dataset_ids VARCHAR[],
            source_run_ids VARCHAR[],
            source_run_refs VARCHAR[],
            PRIMARY KEY (source_id, target_id, relationship_name)
        )
        """
        create_metadata_table = f"""
        CREATE TABLE IF NOT EXISTS {self._metadata_table} (
            key VARCHAR PRIMARY KEY,
            value VARCHAR
        )
        """
        await self._execute_transaction(
            [
                (f"CREATE SCHEMA IF NOT EXISTS {self._schema}", None),
                (create_node_table, None),
                (create_edge_table, None),
                (create_metadata_table, None),
            ]
        )

    def _parse_node_row(self, row) -> dict[str, Any]:
        properties = json.loads(row[3]) if isinstance(row[3], str) else (row[3] if row[3] else {})
        return {**properties, "id": row[0], "name": row[1], "type": row[2]}

    async def query(self, query: str, params: dict[str, Any]) -> list[Any]:
        """[GRAPH] Execute a query against the graph."""
        raise NotImplementedError("Cypher queries not supported in DuckDB.")

    async def add_nodes(
        self,
        nodes: list[DataPoint] | list[str],
        properties: list[dict[str, Any]] | None = None,
        source_ref_key: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        """[GRAPH] Add multiple nodes to the graph."""
        if not nodes:
            return

        queries = []

        for i, node in enumerate(nodes):
            if isinstance(node, tuple):
                props = {**(node[1] or {}), "id": node[0]}
            elif hasattr(node, "model_dump"):
                props = node.model_dump()
            elif hasattr(node, "dict"):
                props = node.dict()
            else:
                props = vars(node) if hasattr(node, "__dict__") else {"id": str(node)}

            node_id = str(props.get("id", str(node)))
            node_name = props.get("name", node_id)
            node_type = props.get(
                "type", node.__class__.__name__ if hasattr(node, "__class__") else None
            )

            if properties and i < len(properties) and properties[i]:
                props.update(properties[i])

            core_keys = {"id", "name", "type"}
            extra = {k: v for k, v in props.items() if k not in core_keys}
            props_json = json.dumps(serialize_for_json(extra))

            existing = await self._execute_query_one(
                f"SELECT source_ref_keys, source_run_refs, properties FROM {self._node_table} WHERE id = $1",
                [node_id],
            )
            existing_ref_keys = list(existing[0]) if existing and existing[0] else []
            existing_run_refs = list(existing[1]) if existing and existing[1] else []

            if existing and existing[2]:
                existing_props = (
                    json.loads(existing[2])
                    if isinstance(existing[2], str)
                    else (existing[2] if existing[2] else {})
                )
                existing_props.update(extra)
                props_json = json.dumps(serialize_for_json(existing_props))

            from cognee.infrastructure.databases.provenance.source_ref_state import (
                provenance_after_attach,
            )

            p = provenance_after_attach(
                existing_ref_keys,
                existing_run_refs,
                [source_ref_key] if source_ref_key else [],
                pipeline_run_id,
            )

            q = f"""
            INSERT INTO {self._node_table} (id, name, type, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                properties = EXCLUDED.properties,
                source_ref_keys = EXCLUDED.source_ref_keys,
                source_dataset_ids = EXCLUDED.source_dataset_ids,
                source_run_ids = EXCLUDED.source_run_ids,
                source_run_refs = EXCLUDED.source_run_refs
            """
            queries.append(
                (
                    q,
                    [
                        node_id,
                        node_name,
                        node_type,
                        props_json,
                        p.source_ref_keys,
                        p.source_dataset_ids,
                        p.source_run_ids,
                        p.source_run_refs,
                    ],
                )
            )

        await self._execute_transaction(queries)

    async def add_node(
        self,
        node: DataPoint | str,
        properties: dict[str, Any] | None = None,
        source_ref_key: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        """Documentation."""
        await self.add_nodes(
            [node],
            [properties] if properties else None,
            source_ref_key,
            pipeline_run_id,
        )

    async def delete_node(self, node_id: str) -> None:
        """Documentation."""
        await self.delete_nodes([node_id])

    async def delete_nodes(self, node_ids: list[str]) -> None:
        """Documentation."""
        if not node_ids:
            return
        p1 = ", ".join([f"${i + 1}" for i in range(len(node_ids))])
        p2 = ", ".join([f"${i + 1 + len(node_ids)}" for i in range(len(node_ids))])
        q_edges = f"DELETE FROM {self._edge_table} WHERE source_id IN ({p1}) OR target_id IN ({p2})"
        q_nodes = f"DELETE FROM {self._node_table} WHERE id IN ({p1})"
        await self._execute_transaction([(q_edges, node_ids + node_ids), (q_nodes, node_ids)])

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Documentation."""
        res = await self._execute_query_one(
            f"SELECT id, name, type, properties FROM {self._node_table} WHERE id = $1", [node_id]
        )
        return self._parse_node_row(res) if res else None

    async def get_nodes(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Documentation."""
        placeholders = ", ".join([f"${i + 1}" for i in range(len(node_ids))])
        res = await self._execute_query(
            f"SELECT id, name, type, properties FROM {self._node_table} WHERE id IN ({placeholders})",
            node_ids,
        )
        return [self._parse_node_row(row) for row in res]

    async def add_edges(
        self,
        edges: list[EdgeData] | list[tuple[str, str, str, dict[str, Any] | None]],
        source_ref_key: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        """Documentation."""
        if not edges:
            return
        queries = []
        for edge_item in edges:
            src, tgt, rel = edge_item[0], edge_item[1], edge_item[2]
            props = edge_item[3] if len(edge_item) > 3 and edge_item[3] else {}
            props_json = json.dumps(serialize_for_json(props))

            existing = await self._execute_query_one(
                f"SELECT source_ref_keys, source_run_refs, properties FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
                [src, tgt, rel],
            )
            existing_ref_keys = list(existing[0]) if existing and existing[0] else []
            existing_run_refs = list(existing[1]) if existing and existing[1] else []

            if existing and existing[2]:
                existing_props = (
                    json.loads(existing[2])
                    if isinstance(existing[2], str)
                    else (existing[2] if existing[2] else {})
                )
                existing_props.update(props)
                props_json = json.dumps(serialize_for_json(existing_props))

            from cognee.infrastructure.databases.provenance.source_ref_state import (
                provenance_after_attach,
            )

            p = provenance_after_attach(
                existing_ref_keys,
                existing_run_refs,
                [source_ref_key] if source_ref_key else [],
                pipeline_run_id,
            )

            q = f"""
            INSERT INTO {self._edge_table} (source_id, target_id, relationship_name, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (source_id, target_id, relationship_name) DO UPDATE SET
                properties = EXCLUDED.properties,
                source_ref_keys = EXCLUDED.source_ref_keys,
                source_dataset_ids = EXCLUDED.source_dataset_ids,
                source_run_ids = EXCLUDED.source_run_ids,
                source_run_refs = EXCLUDED.source_run_refs
            """
            queries.append(
                (
                    q,
                    [
                        src,
                        tgt,
                        rel,
                        props_json,
                        p.source_ref_keys,
                        p.source_dataset_ids,
                        p.source_run_ids,
                        p.source_run_refs,
                    ],
                )
            )
        await self._execute_transaction(queries)

    async def add_edge(
        self,
        node1: str,
        node2: str,
        relationship_name: str,
        properties: dict[str, Any] | None = None,
        source_ref_key: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        """Add a single edge."""
        await self.add_edges(
            [(node1, node2, relationship_name, properties)],
            source_ref_key,
            pipeline_run_id,
        )

    async def delete_graph(self) -> None:
        """Delete the graph."""
        await self._execute_transaction(
            [(f"DELETE FROM {self._edge_table}", None), (f"DELETE FROM {self._node_table}", None)]
        )

    def _parse_edge_row(self, row) -> tuple[str, str, str, dict[str, Any]]:
        """Turn one (source, target, relationship, properties) row into an edge tuple."""
        properties = json.loads(row[3]) if isinstance(row[3], str) else (row[3] if row[3] else {})
        return row[0], row[1], row[2], properties

    def _node_tuple(self, row) -> tuple[str, dict[str, Any]]:
        """Turn one (id, name, type, properties) row into the (id, properties) node shape."""
        return row[0], self._parse_node_row(row)

    async def _induced_subgraph(
        self, node_ids: list[str]
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Return the stored nodes among ``node_ids`` and every edge between two of them.

        Ids with no stored node are left out; an empty id list returns two empty lists.
        """
        if not node_ids:
            return [], []
        nodes_raw = await self._execute_query(
            f"SELECT id, name, type, properties FROM {self._node_table} WHERE list_contains($1, id)",
            [node_ids],
        )
        edges_raw = await self._execute_query(
            f"SELECT source_id, target_id, relationship_name, properties FROM {self._edge_table} "
            "WHERE list_contains($1, source_id) AND list_contains($1, target_id)",
            [node_ids],
        )
        return [self._node_tuple(r) for r in nodes_raw], [
            self._parse_edge_row(r) for r in edges_raw
        ]

    async def get_graph_data(
        self,
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Return every node as (id, properties) and every edge as (source, target, name, props)."""
        nodes_raw = await self._execute_query(
            f"SELECT id, name, type, properties FROM {self._node_table}"
        )
        edges_raw = await self._execute_query(
            f"SELECT source_id, target_id, relationship_name, properties FROM {self._edge_table}"
        )
        return [self._node_tuple(r) for r in nodes_raw], [
            self._parse_edge_row(r) for r in edges_raw
        ]

    async def get_id_filtered_graph_data(
        self, target_ids: list[str]
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Return the nodes with the given ids and the edges between them, as get_graph_data."""
        return await self._induced_subgraph([str(target_id) for target_id in target_ids])

    async def get_filtered_graph_data(
        self, attribute_filters: list[dict[str, list[str | int]]]
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Return nodes whose id, name or type match every filter, and the edges between them.

        No filters returns the whole graph; any other attribute raises ValueError.
        """
        if not attribute_filters:
            return await self.get_graph_data()

        filters = []
        for filter_dict in attribute_filters:
            for attr, filter_values in filter_dict.items():
                if attr not in {"id", "name", "type"}:
                    raise ValueError(f"Invalid filter attribute: {attr!r}")
                filters.append((attr, {str(value) for value in filter_values}))

        if not filters:
            return await self.get_graph_data()

        nodes_raw = await self._execute_query(
            f"SELECT id, name, type, properties FROM {self._node_table}"
        )
        matched_ids = [
            r[0]
            for r in nodes_raw
            if all(
                str({"id": r[0], "name": r[1], "type": r[2]}[attribute]) in values
                for attribute, values in filters
            )
        ]
        return await self._induced_subgraph(matched_ids)

    async def get_graph_metrics(self, include_optional: bool = False) -> dict[str, Any]:
        """Return the graph metrics Cognee stores per pipeline run.

        Keys match the other SQL adapters; the path-based metrics this adapter does
        not compute are -1, and ``num_selfloops`` is -1 unless ``include_optional``.
        """
        nodes_raw = await self._execute_query(f"SELECT id FROM {self._node_table}")
        edges_raw = await self._execute_query(
            f"SELECT source_id, target_id FROM {self._edge_table}"
        )
        node_ids = [r[0] for r in nodes_raw]
        edge_pairs = [(r[0], r[1]) for r in edges_raw]
        num_nodes = len(node_ids)
        num_edges = len(edge_pairs)
        component_sizes = _component_sizes(node_ids, edge_pairs)

        return {
            "num_nodes": num_nodes,
            "num_edges": num_edges,
            "mean_degree": (2 * num_edges) / num_nodes if num_nodes else None,
            "edge_density": num_edges / (num_nodes * (num_nodes - 1)) if num_nodes > 1 else 0,
            "num_connected_components": len(component_sizes),
            "sizes_of_connected_components": component_sizes,
            "num_selfloops": sum(source == target for source, target in edge_pairs)
            if include_optional
            else -1,
            "diameter": -1,
            "avg_shortest_path_length": -1,
            "avg_clustering": -1,
        }

    async def get_graph_counts(self) -> tuple[int, int]:
        """Get graph counts."""
        node_res = await self._execute_query_one(f"SELECT COUNT(*) FROM {self._node_table}")
        edge_res = await self._execute_query_one(f"SELECT COUNT(*) FROM {self._edge_table}")
        return node_res[0] if node_res else 0, edge_res[0] if edge_res else 0

    async def has_edge(self, source_id: str, target_id: str, relationship_name: str) -> bool:
        """Documentation."""
        res = await self._execute_query_one(
            f"SELECT 1 FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
            [source_id, target_id, relationship_name],
        )
        return res is not None

    async def has_edges(self, edges: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
        """Return the given (source, target, relationship) triples that are stored."""
        result = []
        for src, tgt, rel in edges:
            identity = (str(src), str(tgt), str(rel))
            if await self.has_edge(*identity):
                result.append(identity)
        return result

    async def _incident_edge_rows(self, node_id: str) -> list[Any]:
        """Read every edge touching ``node_id`` with both endpoint nodes, in one query."""
        q = f"""
        SELECT
            s.id, s.name, s.type, s.properties,
            e.relationship_name, e.properties,
            t.id, t.name, t.type, t.properties
        FROM {self._edge_table} e
        JOIN {self._node_table} s ON e.source_id = s.id
        JOIN {self._node_table} t ON e.target_id = t.id
        WHERE e.source_id = $1 OR e.target_id = $1
        """
        return await self._execute_query(q, [node_id])

    async def get_edges(self, node_id: str) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
        """Return every edge touching the node as (source node, relationship name, target node)."""
        return [
            (
                self._parse_node_row((r[0], r[1], r[2], r[3])),
                r[4],
                self._parse_node_row((r[6], r[7], r[8], r[9])),
            )
            for r in await self._incident_edge_rows(str(node_id))
        ]

    async def get_neighbors(self, node_id: str) -> list[dict[str, Any]]:
        """Get neighbors."""
        q = f"""
        SELECT DISTINCT n.id, n.name, n.type, n.properties
        FROM {self._node_table} n
        JOIN {self._edge_table} e ON n.id = e.target_id OR n.id = e.source_id
        WHERE (e.source_id = $1 OR e.target_id = $1) AND n.id != $1
        """
        res = await self._execute_query(q, [node_id])
        return [self._parse_node_row(r) for r in res]

    async def get_neighborhood(
        self,
        node_ids: list[str],
        depth: int = 1,
        edge_types: list[str] | None = None,
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Walk incident edges breadth-first from the seed nodes and return the induced subgraph.

        ``depth`` is the number of hops; ``edge_types`` limits which relationships are
        walked, not which edges between reached nodes are returned. Negative depth
        raises ValueError and no seeds returns two empty lists.
        """
        if depth < 0:
            raise ValueError("depth must be non-negative")
        if not node_ids:
            return [], []

        reached = {str(node_id) for node_id in node_ids}
        frontier = set(reached)
        hop = (
            f"SELECT source_id, target_id FROM {self._edge_table} "
            "WHERE (list_contains($1, source_id) OR list_contains($1, target_id))"
        )
        if edge_types:
            hop += " AND list_contains($2, relationship_name)"
        for _ in range(depth):
            if not frontier:
                break
            params: list[Any] = [sorted(frontier)]
            if edge_types:
                params.append([str(edge_type) for edge_type in edge_types])
            next_frontier = set()
            for source_id, target_id in await self._execute_query(hop, params):
                for endpoint in (source_id, target_id):
                    if endpoint not in reached:
                        reached.add(endpoint)
                        next_frontier.add(endpoint)
            frontier = next_frontier

        return await self._induced_subgraph(sorted(reached))

    async def get_nodeset_subgraph(
        self,
        node_type: type[Any],
        node_name: list[str],
        node_name_filter_operator: str = "OR",
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Return the named nodes of ``node_type``, their qualifying neighbours, and edges between.

        With "OR" a neighbour of any named node qualifies; with "AND" only a neighbour
        of every named node does. Any other operator raises ValueError.
        """
        if node_name_filter_operator not in {"OR", "AND"}:
            raise ValueError("node_name_filter_operator must be 'OR' or 'AND'")
        if not node_name:
            return [], []

        primary_rows = await self._execute_query(
            f"SELECT id FROM {self._node_table} WHERE type = $1 AND list_contains($2, name)",
            [node_type.__name__, [str(name) for name in node_name]],
        )
        primary_ids = {r[0] for r in primary_rows}
        if not primary_ids:
            return [], []

        incident = await self._execute_query(
            f"SELECT source_id, target_id FROM {self._edge_table} "
            "WHERE list_contains($1, source_id) OR list_contains($1, target_id)",
            [sorted(primary_ids)],
        )
        neighbor_ids = _select_nodeset_neighbor_ids(
            primary_ids, [(r[0], r[1]) for r in incident], node_name_filter_operator
        )
        return await self._induced_subgraph(sorted(primary_ids | neighbor_ids))

    async def get_connections(
        self, node_id: str | UUID
    ) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
        """Return every edge touching the node as (source node, edge, target node).

        The edge dictionary holds the stored edge properties and ``relationship_name``;
        the stored relationship column always wins over a property of the same name.
        """
        connections = []
        for r in await self._incident_edge_rows(str(node_id)):
            edge_properties = json.loads(r[5]) if isinstance(r[5], str) else (r[5] if r[5] else {})
            connections.append(
                (
                    self._parse_node_row((r[0], r[1], r[2], r[3])),
                    {**edge_properties, "relationship_name": r[4]},
                    self._parse_node_row((r[6], r[7], r[8], r[9])),
                )
            )
        return connections

    async def is_empty(self) -> bool:
        """Check if the graph is empty."""
        """Check if graph is empty."""
        res = await self._execute_query_one(f"SELECT 1 FROM {self._node_table} LIMIT 1")
        return res is None

    async def get_top_degree_node_ids(self, top_k: int) -> list[str]:
        """Get top degree node ids."""
        q = f"""
        SELECT id FROM (
            SELECT source_id AS id, COUNT(*) AS degree
            FROM {self._edge_table}
            GROUP BY source_id
            UNION ALL
            SELECT target_id AS id, COUNT(*) AS degree
            FROM {self._edge_table}
            GROUP BY target_id
        ) combined
        GROUP BY id
        ORDER BY SUM(degree) DESC
        LIMIT $1
        """
        res = await self._execute_query(q, [top_k])
        return [r[0] for r in res]

    async def get_entity_type_names(self, entity_ids: list[str]) -> dict[str, str]:
        """Documentation."""
        if not entity_ids:
            return {}
        placeholders = ", ".join([f"${i + 1}" for i in range(len(entity_ids))])
        q = f"SELECT e.source_id, n.name FROM {self._edge_table} e JOIN {self._node_table} n ON e.target_id = n.id WHERE e.source_id IN ({placeholders}) AND e.relationship_name = 'is_a'"
        res = await self._execute_query(q, entity_ids)
        return {r[0]: r[1] for r in res}

    async def get_triplets_batch(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """Documentation."""
        q = f"SELECT e.source_id, e.relationship_name, e.target_id FROM {self._edge_table} e LIMIT $1 OFFSET $2"
        res = await self._execute_query(q, [limit, offset])
        return [{"source_id": r[0], "relationship_name": r[1], "target_id": r[2]} for r in res]

    async def delete_edge_triples(self, edges: list[Any]) -> None:
        """Documentation."""
        queries = []
        for edge in edges:
            q = f"DELETE FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3"
            queries.append((q, [edge.source_id, edge.target_id, edge.relationship_name]))
        await self._execute_transaction(queries)

    async def remove_belongs_to_set_tags(
        self, tags: list[str], node_ids: list[str] | None = None
    ) -> None:
        """Remove tags from belongs_to_set property array."""
        if not tags:
            return
        if node_ids is not None and not node_ids:
            return

        tags_to_remove = set(tags)
        if node_ids is None:
            q = f"SELECT id, properties FROM {self._node_table}"
            res = await self._execute_query(q)
        else:
            p1 = ", ".join([f"${i + 1}" for i in range(len(node_ids))])
            q = f"SELECT id, properties FROM {self._node_table} WHERE id IN ({p1})"
            res = await self._execute_query(q, node_ids)

        queries = []
        for r in res:
            props = json.loads(r[1]) if isinstance(r[1], str) else r[1]
            if not props or "belongs_to_set" not in props:
                continue
            belongs_to = props["belongs_to_set"]
            if isinstance(belongs_to, list):
                remaining = [t for t in belongs_to if t not in tags_to_remove]
                if len(remaining) != len(belongs_to):
                    props["belongs_to_set"] = remaining
                    props_json = json.dumps(serialize_for_json(props))
                    queries.append(
                        (
                            f"UPDATE {self._node_table} SET properties = $1 WHERE id = $2",
                            [props_json, r[0]],
                        )
                    )

        if queries:
            await self._execute_transaction(queries)

    async def attach_node_source_refs(
        self,
        node_ids: list[str],
        source_ref_keys: list[str],
        pipeline_run_id: str | None = None,
    ) -> None:
        """Documentation."""
        if not source_ref_keys:
            return
        queries = []
        from cognee.infrastructure.databases.provenance.source_ref_state import (
            provenance_after_attach,
        )

        for node_id in sorted({str(node_id) for node_id in node_ids}):
            existing = await self._execute_query_one(
                f"SELECT source_ref_keys, source_run_refs FROM {self._node_table} WHERE id = $1",
                [node_id],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_attach(
                existing_ref_keys, existing_run_refs, source_ref_keys, pipeline_run_id
            )
            q = f"UPDATE {self._node_table} SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE id = $5"
            queries.append(
                (
                    q,
                    [
                        p.source_ref_keys,
                        p.source_dataset_ids,
                        p.source_run_ids,
                        p.source_run_refs,
                        node_id,
                    ],
                )
            )
        await self._execute_transaction(queries)

    async def attach_edge_source_refs(
        self,
        edges: list[Any],
        source_ref_keys: list[str],
        pipeline_run_id: str | None = None,
    ) -> None:
        """Documentation."""
        if not source_ref_keys:
            return
        queries = []
        from cognee.infrastructure.databases.provenance.source_ref_state import (
            provenance_after_attach,
        )

        for edge in sorted(
            {(str(e.source_id), str(e.target_id), str(e.relationship_name)) for e in edges}
        ):
            existing = await self._execute_query_one(
                f"SELECT source_ref_keys, source_run_refs FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
                [edge[0], edge[1], edge[2]],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_attach(
                existing_ref_keys, existing_run_refs, source_ref_keys, pipeline_run_id
            )
            q = f"UPDATE {self._edge_table} SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE source_id = $5 AND target_id = $6 AND relationship_name = $7"
            queries.append(
                (
                    q,
                    [
                        p.source_ref_keys,
                        p.source_dataset_ids,
                        p.source_run_ids,
                        p.source_run_refs,
                        edge[0],
                        edge[1],
                        edge[2],
                    ],
                )
            )
        await self._execute_transaction(queries)

    async def remove_node_source_refs(
        self, node_ids: list[str], source_ref_keys: list[str]
    ) -> None:
        """Documentation."""
        if not source_ref_keys:
            return
        queries = []
        from cognee.infrastructure.databases.provenance.source_ref_state import (
            provenance_after_remove,
        )

        for node_id in sorted({str(node_id) for node_id in node_ids}):
            existing = await self._execute_query_one(
                f"SELECT source_ref_keys, source_run_refs FROM {self._node_table} WHERE id = $1",
                [node_id],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_remove(existing_ref_keys, existing_run_refs, source_ref_keys)
            q = f"UPDATE {self._node_table} SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE id = $5"
            queries.append(
                (
                    q,
                    [
                        p.source_ref_keys,
                        p.source_dataset_ids,
                        p.source_run_ids,
                        p.source_run_refs,
                        node_id,
                    ],
                )
            )
        await self._execute_transaction(queries)

    async def remove_edge_source_refs(self, edges: list[Any], source_ref_keys: list[str]) -> None:
        """Documentation."""
        if not source_ref_keys:
            return
        queries = []
        from cognee.infrastructure.databases.provenance.source_ref_state import (
            provenance_after_remove,
        )

        for edge in sorted(
            {(str(e.source_id), str(e.target_id), str(e.relationship_name)) for e in edges}
        ):
            existing = await self._execute_query_one(
                f"SELECT source_ref_keys, source_run_refs FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
                [edge[0], edge[1], edge[2]],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_remove(existing_ref_keys, existing_run_refs, source_ref_keys)
            q = f"UPDATE {self._edge_table} SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE source_id = $5 AND target_id = $6 AND relationship_name = $7"
            queries.append(
                (
                    q,
                    [
                        p.source_ref_keys,
                        p.source_dataset_ids,
                        p.source_run_ids,
                        p.source_run_refs,
                        edge[0],
                        edge[1],
                        edge[2],
                    ],
                )
            )
        await self._execute_transaction(queries)

    async def get_node_source_refs(self, node_id: str) -> list[str]:
        """Documentation."""
        existing = await self._execute_query_one(
            f"SELECT source_ref_keys FROM {self._node_table} WHERE id = $1", [node_id]
        )
        return list(existing[0]) if existing and existing[0] else []

    async def get_edge_source_refs(
        self, source_id: str, target_id: str, relationship_name: str
    ) -> list[str]:
        """Documentation."""
        existing = await self._execute_query_one(
            f"SELECT source_ref_keys FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
            [source_id, target_id, relationship_name],
        )
        return list(existing[0]) if existing and existing[0] else []

    async def get_node_delete_data(self, node_ids: list[str]) -> dict[str, Any]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import (
            NodeDeleteData,
        )

        q = f"SELECT id, name, type, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs FROM {self._node_table} WHERE id = $1"
        res = {}
        for nid in node_ids:
            row = await self._execute_query_one(q, [nid])
            if row:
                props = (
                    (json.loads(row[3]) if isinstance(row[3], str) else row[3]) if row[3] else {}
                )
                res[nid] = NodeDeleteData(
                    node_id=row[0],
                    node_type=row[2],
                    indexed_fields=["name"] if row[1] else [],
                    node_properties={
                        **props,
                        "id": row[0],
                        "name": row[1],
                        "type": row[2],
                    },
                    source_ref_keys=list(row[4]) if row[4] else [],
                    source_dataset_ids=list(row[5]) if row[5] else [],
                    source_run_ids=list(row[6]) if row[6] else [],
                    source_run_refs=list(row[7]) if row[7] else [],
                )
        return res

    async def get_edge_delete_data(self, edges: list[Any]) -> dict[Any, Any]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import (
            EdgeDeleteData,
            EdgeIdentity,
        )

        q = f"SELECT source_id, target_id, relationship_name, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs FROM {self._edge_table} WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3"
        res = {}
        for e in edges:
            row = await self._execute_query_one(q, [e.source_id, e.target_id, e.relationship_name])
            if row:
                props = (
                    (json.loads(row[3]) if isinstance(row[3], str) else row[3]) if row[3] else {}
                )
                res[e] = EdgeDeleteData(
                    edge=EdgeIdentity(row[0], row[1], row[2]),
                    edge_text=props.get("edge_text", ""),
                    edge_properties=props,
                    source_ref_keys=list(row[4]) if row[4] else [],
                    source_dataset_ids=list(row[5]) if row[5] else [],
                    source_run_ids=list(row[6]) if row[6] else [],
                    source_run_refs=list(row[7]) if row[7] else [],
                )
        return res

    async def find_node_source_refs_by_dataset(self, dataset_id: str) -> dict[str, list[str]]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.source_refs import (
            get_dataset_id_from_source_ref_key,
        )

        q = f"SELECT id, source_ref_keys FROM {self._node_table} WHERE list_contains(source_dataset_ids, $1)"
        res = await self._execute_query(q, [dataset_id])
        out = {}
        for r in res:
            owned = [
                key
                for key in (r[1] or [])
                if str(get_dataset_id_from_source_ref_key(key)) == dataset_id
            ]
            if owned:
                out[r[0]] = owned
        return out

    async def find_edge_source_refs_by_dataset(self, dataset_id: str) -> dict[Any, list[str]]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import EdgeIdentity
        from cognee.infrastructure.databases.provenance.source_refs import (
            get_dataset_id_from_source_ref_key,
        )

        q = f"SELECT source_id, target_id, relationship_name, source_ref_keys FROM {self._edge_table} WHERE list_contains(source_dataset_ids, $1)"
        res = await self._execute_query(q, [dataset_id])
        out = {}
        for r in res:
            owned = [
                key
                for key in (r[3] or [])
                if str(get_dataset_id_from_source_ref_key(key)) == dataset_id
            ]
            if owned:
                out[EdgeIdentity(r[0], r[1], r[2])] = owned
        return out

    async def find_node_source_refs_by_pipeline_run(
        self, pipeline_run_id: str
    ) -> dict[str, list[str]]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.source_refs import (
            get_pipeline_run_id_from_source_run_ref,
            get_source_ref_key_from_source_run_ref,
        )

        q = f"SELECT id, source_run_refs FROM {self._node_table} WHERE list_contains(source_run_ids, $1)"
        res = await self._execute_query(q, [pipeline_run_id])
        out = {}
        for r in res:
            contributed = [
                get_source_ref_key_from_source_run_ref(ref)
                for ref in (r[1] or [])
                if str(get_pipeline_run_id_from_source_run_ref(ref)) == pipeline_run_id
            ]
            if contributed:
                out[r[0]] = contributed
        return out

    async def find_edge_source_refs_by_pipeline_run(
        self, pipeline_run_id: str
    ) -> dict[Any, list[str]]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import EdgeIdentity
        from cognee.infrastructure.databases.provenance.source_refs import (
            get_pipeline_run_id_from_source_run_ref,
            get_source_ref_key_from_source_run_ref,
        )

        q = f"SELECT source_id, target_id, relationship_name, source_run_refs FROM {self._edge_table} WHERE list_contains(source_run_ids, $1)"
        res = await self._execute_query(q, [pipeline_run_id])
        out = {}
        for r in res:
            contributed = [
                get_source_ref_key_from_source_run_ref(ref)
                for ref in (r[3] or [])
                if str(get_pipeline_run_id_from_source_run_ref(ref)) == pipeline_run_id
            ]
            if contributed:
                out[EdgeIdentity(r[0], r[1], r[2])] = contributed
        return out

    async def find_nodes_by_source_ref(self, source_ref_key: str) -> list[str]:
        """Documentation."""
        q = f"SELECT id FROM {self._node_table} WHERE list_contains(source_ref_keys, $1)"
        res = await self._execute_query(q, [source_ref_key])
        return [r[0] for r in res]

    async def find_edges_by_source_ref(self, source_ref_key: str) -> list[Any]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import EdgeIdentity

        q = f"SELECT source_id, target_id, relationship_name FROM {self._edge_table} WHERE list_contains(source_ref_keys, $1)"
        res = await self._execute_query(q, [source_ref_key])
        return [EdgeIdentity(r[0], r[1], r[2]) for r in res]

    async def get_graph_metadata(self) -> dict[str, str]:
        """Documentation."""
        await self.initialize()
        q = f"SELECT key, value FROM {self._metadata_table}"
        res = await self._execute_query(q)
        return {r[0]: r[1] for r in res}

    async def set_graph_metadata(self, metadata: dict[str, str]) -> None:
        """Documentation."""
        if not metadata:
            return
        await self.initialize()
        queries = []
        for k, v in metadata.items():
            q = f"""
            INSERT INTO {self._metadata_table} (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """
            queries.append((q, [str(k), str(v)]))
        await self._execute_transaction(queries)
