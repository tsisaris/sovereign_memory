"""DuckDB Vector and Graph adapter package."""

from typing import ClassVar

"""DuckDB Vector and Graph adapter package."""
import asyncio
import json
from typing import Any, List, Optional
from uuid import UUID

import duckdb
from cognee.infrastructure.databases.graph.graph_db_interface import GraphDBInterface
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

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        database_name: str | None = "cognee",
        embedding_engine: EmbeddingEngine | None = None,
        graph_database_username: str | None = None,
        graph_database_password: str | None = None,
        **kwargs,
    ) -> None:
        """Initialize DuckDB adapter."""
        self.database_url = url
        self.api_key = api_key
        self.database_name = database_name
        self.embedding_engine = embedding_engine
        self.graph_database_username = graph_database_username
        self.graph_database_password = graph_database_password
        self.VECTOR_DB_LOCK = asyncio.Lock()

        # Create in-memory DuckDB connection
        # If database_url is provided, use it; otherwise use in-memory
        if url:
            self.connection = duckdb.connect(url)
        else:
            self.connection = duckdb.connect()  # In-memory database

        pass

    def _setup_extensions(self) -> None:
        """Setup DuckDB extensions."""

        self.connection.execute(
            "LOAD vss;"
        )  # TODO add index: CREATE INDEX my_hnsw_index ON my_vector_table USING HNSW (vec);

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

    async def _execute_transaction(self, queries: list[tuple[str, list[Any] | None]]) -> None:
        """Execute multiple queries in a transaction with async lock."""
        async with self.VECTOR_DB_LOCK:
            try:
                self.connection.execute("BEGIN TRANSACTION")
                for query, params in queries:
                    if params:
                        self.connection.execute(query, params)
                    else:
                        self.connection.execute(query)
                self.connection.execute("COMMIT")
            except duckdb.Error:
                self.connection.execute("ROLLBACK")
                raise

    async def close(self) -> None:
        """Close the DuckDB connection safely."""
        async with self.VECTOR_DB_LOCK:
            if hasattr(self, "connection"):
                self.connection.close()

    # VectorDBInterface methods
    async def embed_data(self, data: list[str]) -> list[list[float]]:
        """[VECTOR] Embed text data using the embedding engine."""
        if not self.embedding_engine:
            raise ValueError("Embedding engine not configured")
        result = await self.embedding_engine.embed_text(data)
        return result  # type: ignore[no-any-return]

    async def has_collection(self, collection_name: str) -> bool:
        """[VECTOR] Check if a collection exists."""
        try:
            # Check if table exists in DuckDB
            result = await self._execute_query_one(
                "SELECT table_name FROM information_schema.tables WHERE table_name = $1",
                [collection_name],
            )
            return result is not None
        except duckdb.Error:
            return False

    async def create_collection(self, collection_name: str, vector_dimension: int = 3072) -> None:
        """[VECTOR] Create a new collection (table) in DuckDB."""
        # Create a table for storing vector data with specified dimension
        vector_dimension = self.embedding_engine.get_vector_size()
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {collection_name} (
            id VARCHAR PRIMARY KEY,
            text TEXT,
            vector FLOAT[{vector_dimension}],
            payload JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        await self._execute_query(create_table_query)

    async def create_data_points(self, collection_name: str, data_points: list[DataPoint]) -> None:
        """[VECTOR] Create data points in the collection."""
        # TODO: Implement DuckDB data point creation
        if not await self.has_collection(collection_name):
            raise CollectionNotFoundError(f"Collection {collection_name} not found!")

        data_vectors = await self.embed_data(
            [DataPoint.get_embeddable_data(data_point) for data_point in data_points]
        )

        # Create the data points (use INSERT OR REPLACE to handle duplicates)
        create_data_points_query = f"""
        INSERT OR REPLACE INTO {collection_name} (id, text, vector, payload) VALUES ($1, $2, $3, $4)
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

    async def create_vector_index(self, index_name: str, index_property_name: str) -> None:
        """[VECTOR] Create a vector index for a specific property."""
        # TODO: Implement DuckDB vector index creation
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
                query = f"SELECT payload FROM {collection_name} WHERE id = $1"
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
            search_query = f"""select count(*) from {collection_name}"""
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
                where_clause = f"WHERE {list_fn}({belongs_expr}, $1)"
                query_params.append(node_name)

            search_query = f"""
            SELECT {", ".join(select_fields)}
            FROM {collection_name}
            {where_clause}
            ORDER BY distance ASC
            LIMIT {limit}
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
                    vector=row[2] if with_vector else None,
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
        self, collection_name: str, data_point_ids: list[str]
    ) -> dict[str, int]:
        """[VECTOR] Delete data points by their IDs."""
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
            delete_query = f"DELETE FROM {collection_name} WHERE id IN ({placeholders})"

            # Execute the deletion
            await self._execute_query(delete_query, data_point_ids)

            # Get the count of deleted rows (DuckDB doesn't return this directly, so we approximate)
            deleted_count = len(data_point_ids)  # Assume all were deleted for simplicity

            logger.info(f"Deleted {deleted_count} data points from collection {collection_name}")
            return {"deleted": deleted_count}

        except duckdb.Error as e:
            logger.error(f"Error deleting data points: {str(e)}")
            raise e

    async def prune(self) -> None:
        """Documentation."""
        try:
            q = "SELECT table_name FROM information_schema.columns WHERE column_name = 'vector'"
            res = await self._execute_query(q)
            tables = [r[0] for r in res]
            for table in tables:
                await self._execute_query(f"DROP TABLE IF EXISTS {table}")
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
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        )
        tables_result = await self._execute_query(tables_query)
        for row in tables_result:
            table_name = row[0] if isinstance(row, list | tuple) else row
            collection_names.append(table_name)

        return collection_names

    # GraphDBInterface methods

    # GraphDBInterface methods

    async def initialize(self) -> None:
        """[GRAPH] Initialize the graph database schema."""
        create_node_table = """
        CREATE TABLE IF NOT EXISTS graph_node (
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
        create_edge_table = """
        CREATE TABLE IF NOT EXISTS graph_edge (
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
        create_metadata_table = """
        CREATE TABLE IF NOT EXISTS graph_metadata (
            key VARCHAR PRIMARY KEY,
            value VARCHAR
        )
        """
        await self._execute_transaction(
            [
                (create_node_table, None),
                (create_edge_table, None),
                (create_metadata_table, None),
            ]
        )

    def _parse_node_row(self, row) -> dict[str, Any]:
        properties = json.loads(row[3]) if isinstance(row[3], str) else (row[3] if row[3] else {})
        return {"id": row[0], "name": row[1], "type": row[2], **properties}

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
                "SELECT source_ref_keys, source_run_refs, properties FROM graph_node WHERE id = $1",
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

            q = """
            INSERT INTO graph_node (id, name, type, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs) 
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
        q_edges = f"DELETE FROM graph_edge WHERE source_id IN ({p1}) OR target_id IN ({p2})"
        q_nodes = f"DELETE FROM graph_node WHERE id IN ({p1})"
        await self._execute_transaction([(q_edges, node_ids + node_ids), (q_nodes, node_ids)])

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Documentation."""
        res = await self._execute_query_one(
            "SELECT id, name, type, properties FROM graph_node WHERE id = $1", [node_id]
        )
        return self._parse_node_row(res) if res else None

    async def get_nodes(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Documentation."""
        placeholders = ", ".join([f"${i + 1}" for i in range(len(node_ids))])
        res = await self._execute_query(
            f"SELECT id, name, type, properties FROM graph_node WHERE id IN ({placeholders})",
            node_ids,
        )
        return [self._parse_node_row(row) for row in res]

    async def add_edges(
        self,
        edges: list[tuple[str, str, str, dict[str, Any] | None]],
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
                "SELECT source_ref_keys, source_run_refs, properties FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
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

            q = """
            INSERT INTO graph_edge (source_id, target_id, relationship_name, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs)
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
            [("DELETE FROM graph_edge", None), ("DELETE FROM graph_node", None)]
        )

    async def get_graph_data(
        self,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Get all graph data."""
        nodes_raw = await self._execute_query("SELECT id, name, type, properties FROM graph_node")
        edges_raw = await self._execute_query(
            "SELECT source_id, target_id, relationship_name, properties FROM graph_edge"
        )
        nodes = [self._parse_node_row(r) for r in nodes_raw]
        edges = [
            (
                r[0],
                r[1],
                r[2],
                json.loads(r[3]) if isinstance(r[3], str) else (r[3] if r[3] else {}),
            )
            for r in edges_raw
        ]
        return nodes, edges

    async def get_id_filtered_graph_data(
        self, target_ids: list[str]
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Documentation."""
        if not target_ids:
            return [], []
        p1 = ", ".join([f"${i + 1}" for i in range(len(target_ids))])
        p2 = ", ".join([f"${i + 1 + len(target_ids)}" for i in range(len(target_ids))])
        nodes_raw = await self._execute_query(
            f"SELECT id, name, type, properties FROM graph_node WHERE id IN ({p1})",
            target_ids,
        )
        edges_raw = await self._execute_query(
            f"SELECT source_id, target_id, relationship_name, properties FROM graph_edge WHERE source_id IN ({p1}) AND target_id IN ({p2})",
            target_ids + target_ids,
        )
        nodes = [self._parse_node_row(r) for r in nodes_raw]
        edges = [
            (
                r[0],
                r[1],
                r[2],
                json.loads(r[3]) if isinstance(r[3], str) else (r[3] if r[3] else {}),
            )
            for r in edges_raw
        ]
        return nodes, edges

    async def get_filtered_graph_data(
        self, attribute_filters: list[dict[str, list[str | int]]]
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Documentation."""
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

        nodes_raw = await self._execute_query("SELECT id, name, type, properties FROM graph_node")
        nodes = []
        valid_node_ids = set()
        for r in nodes_raw:
            row_dict = {"id": r[0], "name": r[1], "type": r[2]}
            if all(str(row_dict[attribute]) in values for attribute, values in filters):
                nodes.append(self._parse_node_row(r))
                valid_node_ids.add(r[0])

        if not valid_node_ids:
            return [], []

        edges_raw = await self._execute_query(
            "SELECT source_id, target_id, relationship_name, properties FROM graph_edge"
        )
        edges = []
        for r in edges_raw:
            if r[0] in valid_node_ids and r[1] in valid_node_ids:
                props = json.loads(r[3]) if isinstance(r[3], str) else (r[3] if r[3] else {})
                edges.append((r[0], r[1], r[2], props))

        return nodes, edges

    async def get_graph_metrics(self, include_optional: bool = False) -> dict[str, Any]:
        """Documentation."""
        nodes_raw = await self._execute_query("SELECT id FROM graph_node")
        edges_raw = await self._execute_query("SELECT source_id, target_id FROM graph_edge")
        node_count = len(nodes_raw)
        edge_count = len(edges_raw)

        from collections import defaultdict

        graph = defaultdict(list)
        for e in edges_raw:
            graph[e[0]].append(e[1])
            graph[e[1]].append(e[0])

        visited = set()
        components = 0
        for node in nodes_raw:
            nid = node[0]
            if nid not in visited:
                components += 1
                queue = [nid]
                while queue:
                    curr = queue.pop(0)
                    if curr not in visited:
                        visited.add(curr)
                        queue.extend(graph[curr])

        density = (edge_count / (node_count * (node_count - 1))) if node_count > 1 else 0.0
        average_degree = (2 * edge_count / node_count) if node_count > 0 else 0.0

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "density": density,
            "number_of_connected_components": components,
            "average_degree": average_degree,
        }

    async def get_graph_counts(self) -> tuple[int, int]:
        """Get graph counts."""
        node_res = await self._execute_query_one("SELECT COUNT(*) FROM graph_node")
        edge_res = await self._execute_query_one("SELECT COUNT(*) FROM graph_edge")
        return node_res[0] if node_res else 0, edge_res[0] if edge_res else 0

    async def has_edge(self, source_id: str, target_id: str, relationship_name: str) -> bool:
        """Documentation."""
        res = await self._execute_query_one(
            "SELECT 1 FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
            [source_id, target_id, relationship_name],
        )
        return res is not None

    async def has_edges(self, edges: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
        """Documentation."""
        result = []
        for src, tgt, rel in edges:
            if await self.has_edge(src, tgt, rel):
                result.append((src, tgt, rel))
        return result

    async def get_edges(self, node_id: str) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
        """Get all edges."""
        q = """
        SELECT 
            s.id, s.name, s.type, s.properties,
            e.relationship_name,
            t.id, t.name, t.type, t.properties
        FROM graph_edge e
        JOIN graph_node s ON e.source_id = s.id
        JOIN graph_node t ON e.target_id = t.id
        WHERE e.source_id = $1 OR e.target_id = $1
        """
        res = await self._execute_query(q, [node_id])
        out = []
        for r in res:
            source_dict = self._parse_node_row((r[0], r[1], r[2], r[3]))
            rel_name = r[4]
            target_dict = self._parse_node_row((r[5], r[6], r[7], r[8]))
            out.append((source_dict, rel_name, target_dict))
        return out

    async def get_neighbors(self, node_id: str) -> list[dict[str, Any]]:
        """Get neighbors."""
        q = """
        SELECT DISTINCT n.id, n.name, n.type, n.properties
        FROM graph_node n
        JOIN graph_edge e ON n.id = e.target_id OR n.id = e.source_id
        WHERE (e.source_id = $1 OR e.target_id = $1) AND n.id != $1
        """
        res = await self._execute_query(q, [node_id])
        return [self._parse_node_row(r) for r in res]

    async def get_neighborhood(
        self, node_id: str, k: int = 1, property_keys: list[str] | None = None
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Get neighborhood."""
        q = f"""
        WITH RECURSIVE
        paths(id, depth) AS (
            SELECT $1::VARCHAR, 0
            UNION ALL
            SELECT 
                CASE WHEN e.source_id = p.id THEN e.target_id ELSE e.source_id END,
                p.depth + 1
            FROM paths p
            JOIN graph_edge e ON p.id = e.source_id OR p.id = e.target_id
            WHERE p.depth < $2
        )
        SELECT DISTINCT id FROM paths
        """
        res = await self._execute_query(q, [node_id, k])
        neighbor_ids = [r[0] for r in res]
        if not neighbor_ids:
            return [], []

        placeholders = ", ".join([f"${i + 1}" for i in range(len(neighbor_ids))])
        placeholders2 = ", ".join(
            [f"${i + 1 + len(neighbor_ids)}" for i in range(len(neighbor_ids))]
        )
        q_nodes = f"SELECT id, name, type, properties FROM graph_node WHERE id IN ({placeholders})"
        q_edges = f"SELECT source_id, target_id, relationship_name, properties FROM graph_edge WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders2})"

        nodes_raw = await self._execute_query(q_nodes, neighbor_ids)
        edges_raw = await self._execute_query(q_edges, neighbor_ids + neighbor_ids)

        nodes = [self._parse_node_row(r) for r in nodes_raw]
        edges = [
            (
                r[0],
                r[1],
                r[2],
                json.loads(r[3]) if isinstance(r[3], str) else (r[3] if r[3] else {}),
            )
            for r in edges_raw
        ]
        return nodes, edges

    async def get_nodeset_subgraph(
        self,
        node_type: type[Any],
        node_name: list[str],
        node_name_filter_operator: str = "OR",
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, dict[str, Any]]]]:
        """Documentation."""
        if not node_name:
            return [], []

        placeholders = ", ".join([f"${i + 1}" for i in range(len(node_name))])

        q_ids = f"SELECT id FROM graph_node WHERE name IN ({placeholders})"
        ids_raw = await self._execute_query(q_ids, node_name)
        ids = [r[0] for r in ids_raw]

        if not ids:
            return [], []

        p1 = ", ".join([f"${i + 1}" for i in range(len(ids))])
        p2 = ", ".join([f"${i + 1 + len(ids)}" for i in range(len(ids))])
        q_edges = f"SELECT source_id, target_id FROM graph_edge WHERE source_id IN ({p1}) OR target_id IN ({p2})"
        edges_raw = await self._execute_query(q_edges, ids + ids)

        connected_ids = set(ids)
        for r in edges_raw:
            connected_ids.add(r[0])
            connected_ids.add(r[1])

        # Support AND semantics
        if node_name_filter_operator == "AND":
            # For AND, we only include edges that connect nodes BOTH of which are in the original requested name set
            # The 'ids' list contains exactly the nodes we matched by name.
            filtered_edges = []
            for r in edges_raw:
                if r[0] in ids and r[1] in ids:
                    filtered_edges.append(r)

            connected_ids = set()
            for r in filtered_edges:
                connected_ids.add(r[0])
                connected_ids.add(r[1])

        return await self.get_id_filtered_graph_data(list(connected_ids))

    async def get_connections(
        self, node_id: str | UUID
    ) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Documentation."""
        q = "SELECT source_id, target_id, relationship_name, properties FROM graph_edge WHERE source_id = $1 OR target_id = $1"
        res = await self._execute_query(q, [str(node_id)])
        return [
            (
                r[0],
                r[1],
                r[2],
                json.loads(r[3]) if isinstance(r[3], str) else (r[3] if r[3] else {}),
            )
            for r in res
        ]

    async def is_empty(self) -> bool:
        """Check if the graph is empty."""
        """Check if graph is empty."""
        res = await self._execute_query_one("SELECT 1 FROM graph_node LIMIT 1")
        return res is None

    async def get_top_degree_node_ids(self, top_k: int) -> list[str]:
        """Get top degree node ids."""
        q = """
        SELECT id FROM (
            SELECT source_id AS id, COUNT(*) AS degree
            FROM graph_edge
            GROUP BY source_id
            UNION ALL
            SELECT target_id AS id, COUNT(*) AS degree
            FROM graph_edge
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
        q = f"SELECT e.source_id, n.name FROM graph_edge e JOIN graph_node n ON e.target_id = n.id WHERE e.source_id IN ({placeholders}) AND e.relationship_name = 'is_a'"
        res = await self._execute_query(q, entity_ids)
        return {r[0]: r[1] for r in res}

    async def get_triplets_batch(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """Documentation."""
        q = "SELECT e.source_id, e.relationship_name, e.target_id FROM graph_edge e LIMIT $1 OFFSET $2"
        res = await self._execute_query(q, [limit, offset])
        return [{"source_id": r[0], "relationship_name": r[1], "target_id": r[2]} for r in res]

    async def delete_edge_triples(self, edges: list[Any]) -> None:
        """Documentation."""
        queries = []
        for edge in edges:
            q = "DELETE FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3"
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
            q = "SELECT id, properties FROM graph_node"
            res = await self._execute_query(q)
        else:
            p1 = ", ".join([f"${i + 1}" for i in range(len(node_ids))])
            q = f"SELECT id, properties FROM graph_node WHERE id IN ({p1})"
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
                        ("UPDATE graph_node SET properties = $1 WHERE id = $2", [props_json, r[0]])
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
                "SELECT source_ref_keys, source_run_refs FROM graph_node WHERE id = $1",
                [node_id],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_attach(
                existing_ref_keys, existing_run_refs, source_ref_keys, pipeline_run_id
            )
            q = "UPDATE graph_node SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE id = $5"
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
                "SELECT source_ref_keys, source_run_refs FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
                [edge[0], edge[1], edge[2]],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_attach(
                existing_ref_keys, existing_run_refs, source_ref_keys, pipeline_run_id
            )
            q = "UPDATE graph_edge SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE source_id = $5 AND target_id = $6 AND relationship_name = $7"
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
                "SELECT source_ref_keys, source_run_refs FROM graph_node WHERE id = $1",
                [node_id],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_remove(existing_ref_keys, existing_run_refs, source_ref_keys)
            q = "UPDATE graph_node SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE id = $5"
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
                "SELECT source_ref_keys, source_run_refs FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
                [edge[0], edge[1], edge[2]],
            )
            if existing is None:
                continue
            existing_ref_keys = list(existing[0]) if existing[0] else []
            existing_run_refs = list(existing[1]) if existing[1] else []
            p = provenance_after_remove(existing_ref_keys, existing_run_refs, source_ref_keys)
            q = "UPDATE graph_edge SET source_ref_keys = $1, source_dataset_ids = $2, source_run_ids = $3, source_run_refs = $4 WHERE source_id = $5 AND target_id = $6 AND relationship_name = $7"
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
            "SELECT source_ref_keys FROM graph_node WHERE id = $1", [node_id]
        )
        return list(existing[0]) if existing and existing[0] else []

    async def get_edge_source_refs(
        self, source_id: str, target_id: str, relationship_name: str
    ) -> list[str]:
        """Documentation."""
        existing = await self._execute_query_one(
            "SELECT source_ref_keys FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3",
            [source_id, target_id, relationship_name],
        )
        return list(existing[0]) if existing and existing[0] else []

    async def get_node_delete_data(self, node_ids: list[str]) -> dict[str, Any]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import (
            NodeDeleteData,
        )

        q = "SELECT id, name, type, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs FROM graph_node WHERE id = $1"
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

        q = "SELECT source_id, target_id, relationship_name, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs FROM graph_edge WHERE source_id = $1 AND target_id = $2 AND relationship_name = $3"
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

        q = "SELECT id, source_ref_keys FROM graph_node WHERE list_contains(source_dataset_ids, $1)"
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

        q = "SELECT source_id, target_id, relationship_name, source_ref_keys FROM graph_edge WHERE list_contains(source_dataset_ids, $1)"
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

        q = "SELECT id, source_run_refs FROM graph_node WHERE list_contains(source_run_ids, $1)"
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

        q = "SELECT source_id, target_id, relationship_name, source_run_refs FROM graph_edge WHERE list_contains(source_run_ids, $1)"
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
        q = "SELECT id FROM graph_node WHERE list_contains(source_ref_keys, $1)"
        res = await self._execute_query(q, [source_ref_key])
        return [r[0] for r in res]

    async def find_edges_by_source_ref(self, source_ref_key: str) -> list[Any]:
        """Documentation."""
        from cognee.infrastructure.databases.provenance.delete_data import EdgeIdentity

        q = "SELECT source_id, target_id, relationship_name FROM graph_edge WHERE list_contains(source_ref_keys, $1)"
        res = await self._execute_query(q, [source_ref_key])
        return [EdgeIdentity(r[0], r[1], r[2]) for r in res]

    async def get_graph_metadata(self) -> dict[str, str]:
        """Documentation."""
        await self.initialize()
        q = "SELECT key, value FROM graph_metadata"
        res = await self._execute_query(q)
        return {r[0]: r[1] for r in res}

    async def set_graph_metadata(self, metadata: dict[str, str]) -> None:
        """Documentation."""
        if not metadata:
            return
        await self.initialize()
        queries = []
        for k, v in metadata.items():
            q = """
            INSERT INTO graph_metadata (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """
            queries.append((q, [str(k), str(v)]))
        await self._execute_transaction(queries)
