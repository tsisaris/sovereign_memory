"""Cognee text chunks as the owner's relational tier, written on the owner's own handle.

Every captured event becomes one Cognee DocumentChunk: a graph node carrying its
event provenance as properties (through Cognee's DuckDB graph adapter) and a vector in the
"DocumentChunk_text" collection that Cognee's own chunk search reads, both
written on the owner's borrowed native transaction handle so they commit with
the receipt. The vector comes from the Cognee embedding engine the deployment
configured, called before the transaction. The chunk id derives from the event
id, so a replay overwrites the same chunk. Each chunk belongs to its producer's
node set, which is how a search is limited to the producers the caller may see.

This tier stores and searches chunks only. Cognee's cognify (entity and
relationship extraction) needs a language model and is not run: no entity,
relationship or summary exists, and every hit says so in "ranking".
"""

import asyncio
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any
from uuid import UUID

import duckdb
from cognee_community_hybrid_adapter_duckdb.duckdb_adapter import DuckDBAdapter

from service.memory_processor_seam import ProcessorResult, RetrievalScope
from service.memory_tier_support import (
    actor_of,
    event_instant,
    event_text,
    group_of,
    is_removal,
    removal_target,
)

TIER_NAME = "relational"
PROCESSOR_ID = "cognee-duckdb-chunks"
PROJECTION_VERSION = "cognee-chunk-1"
COLLECTION = "DocumentChunk_text"
NODE_TYPE = "DocumentChunk"
CHUNK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "sovereign-memory-owner/cognee-chunk")
PROVENANCE_FIELDS = (
    "schema_version",
    "event_type",
    "source_revision",
    "source_artifact_id",
    "work_id",
    "payload_sha256",
    "exactness",
)


class EmbeddingIndexMismatch(RuntimeError):
    """The stored chunks were embedded by a different model than the configured one.

    Comparing vectors from different models (even of the same dimension) is
    meaningless, so the tier refuses to write or search until the index is rebuilt
    from the original events. The original events are never altered.
    """


def embedding_identity(engine: Any) -> str:
    """Return the embedding engine's identity: provider, model, dimensions and API version.

    It is stamped on every stored chunk and compared before any write or search.
    Raises ValueError when the engine does not name its model and dimensions.
    """
    model, dimensions = getattr(engine, "model", None), getattr(engine, "dimensions", None)
    if not model or not dimensions:
        raise ValueError("The embedding engine does not name its model and dimensions")
    provider, version_ = getattr(engine, "provider", None), getattr(engine, "api_version", None)
    return f"{provider}|{model}|{dimensions}|{version_ or ''}"


def chunk_id(event_id: str) -> str:
    """Return the deterministic Cognee chunk id of a captured event; pure."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, event_id))


@dataclass(frozen=True)
class PreparedChunk:
    """A chunk and its vector to store, or (when removal_target is set) the chunk to delete.

    Holds plain data only, never a database handle.
    """

    chunk_id: str
    properties: dict[str, Any] | None = None
    vector: list[float] | None = None
    removal_target: str | None = None


class CogneeChunkTier:
    """Owner processor and retriever for the relational tier, backed by Cognee chunks.

    embedding_engine is a Cognee embedding engine (an async embed_text(list[str])
    and get_vector_size()); database_name is the DuckDB schema Cognee's tables
    live in. Both come from the deployment's configuration. The tier never
    opens, commits, rolls back or closes a connection.
    """

    tier_name = TIER_NAME

    def __init__(self, embedding_engine: Any, database_name: str) -> None:
        """Keep the engine and schema name; nothing is opened until the first owner call."""
        if not isinstance(database_name, str) or not database_name:
            raise ValueError("CogneeChunkTier needs a non-empty database_name")
        self.embedding_engine = embedding_engine
        self.embedding_identity = embedding_identity(embedding_engine)
        self.database_name = database_name

    def _adapter(self, handle: duckdb.DuckDBPyConnection) -> DuckDBAdapter:
        """Build Cognee's adapter on the owner's handle for one call; it borrows, never owns."""
        return DuckDBAdapter(
            connection=handle,
            database_name=self.database_name,
            embedding_engine=self.embedding_engine,
        )

    def _require_matching_index(self, adapter: DuckDBAdapter) -> None:
        """Raise EmbeddingIndexMismatch if any stored chunk has another embedding identity; reads only."""
        (exists,) = adapter.connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_catalog = "
            "current_database() AND table_schema = ? AND table_name = ?",
            [self.database_name, COLLECTION],
        ).fetchone()
        if not exists:
            return
        (other,) = adapter.connection.execute(
            f"SELECT count(*) FROM {adapter._table(COLLECTION)} "
            "WHERE json_extract_string(payload, '$.embedding_identity') IS DISTINCT FROM ?",
            [self.embedding_identity],
        ).fetchone()
        if other:
            raise EmbeddingIndexMismatch(
                f"{other} stored chunks were embedded by a model other than "
                f"{self.embedding_identity!r}; rebuild the index from the source events"
            )

    # ---- write seam -------------------------------------------------------------

    async def prepare(
        self, event_envelope: dict[str, Any], verified_payload_bytes: bytes | None
    ) -> PreparedChunk:
        """Embed the event's text (or name the chunk a removal deletes); no database.

        The embedding is a model call and happens here, outside any write
        transaction. A provider failure propagates and the owner records it as
        a retryable failure. Raises TerminalProcessingError for bytes that are
        not UTF-8 text and for a removal that does not match its causation event.
        """
        if is_removal(event_envelope):
            target, _ = removal_target(event_envelope, verified_payload_bytes)
            return PreparedChunk(chunk_id(target), removal_target=target)
        event_id = event_envelope["event_id"]
        actor = actor_of(event_envelope)
        text, _, _ = event_text(event_envelope, verified_payload_bytes)
        vectors = await self.embedding_engine.embed_text([text])
        if len(vectors) != 1 or not vectors[0]:
            raise RuntimeError("The embedding engine did not return one vector for the chunk")
        identifier = chunk_id(event_id)
        properties = {
            "name": event_id,
            "type": NODE_TYPE,
            "text": text,
            "belongs_to_set": [group_of(actor)],
            "event_id": event_id,
            "actor_agent_id": actor,
            "embedding_identity": self.embedding_identity,
            "event_time": event_instant(event_envelope),
            **{
                key: event_envelope.get(key)
                for key in PROVENANCE_FIELDS
                if key not in {"event_type"}
            },
            "event_type": event_envelope.get("event_type", event_envelope.get("content_type")),
        }
        return PreparedChunk(identifier, properties, [float(x) for x in vectors[0]])

    def apply(
        self, native_tx_handle: duckdb.DuckDBPyConnection, prepared_work: PreparedChunk
    ) -> ProcessorResult:
        """Store or delete the chunk's node and vector on the owner's open transaction.

        A removal of a chunk that was never stored reports processed_no_new_fact.
        """
        adapter = self._adapter(native_tx_handle)
        if prepared_work.removal_target is None:
            self._require_matching_index(adapter)
        asyncio.run(adapter.initialize())
        identifier = prepared_work.chunk_id
        if prepared_work.removal_target is not None:
            existed = asyncio.run(adapter.get_node(identifier)) is not None
            asyncio.run(adapter.delete_nodes([identifier]))
            asyncio.run(adapter.delete_data_points(COLLECTION, [UUID(identifier)]))
            status = "processed" if existed else "processed_no_new_fact"
        else:
            properties = prepared_work.properties or {}
            point = {
                "id": identifier,
                "vector": prepared_work.vector,
                "payload": {"id": identifier, **properties},
            }
            asyncio.run(adapter.add_nodes([(identifier, properties)]))
            asyncio.run(adapter.upsert_raw_vectors(COLLECTION, [point]))
            status = "processed"
        return ProcessorResult(status, PROCESSOR_ID, version("cognee"), PROJECTION_VERSION)

    # ---- read seam --------------------------------------------------------------

    async def prepare_query(self, scope: RetrievalScope, query: str, limit: int) -> dict[str, Any]:
        """Embed the question outside the database worker."""
        vectors = await self.embedding_engine.embed_text([query])
        if len(vectors) != 1 or not vectors[0]:
            raise RuntimeError("The embedding engine did not return one vector for the query")
        return {"vector": [float(x) for x in vectors[0]], "limit": limit}

    def retrieve(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return the chunks nearest the question among the producers the visibility permits.

        The search is Cognee's vector search over stored chunks (cosine
        distance, lower is nearer), limited in SQL to the producers' node sets.
        There is no entity or relationship search. Before any chunk was stored
        the answer is no chunks. Raises ValueError for an unknown visibility
        mode; it never writes.
        """
        visibility = scope.visibility
        if visibility.mode == "own":
            groups: list[str] | None = [group_of(scope.agent_id)]
        elif visibility.mode == "granted":
            groups = [group_of(actor) for actor in sorted(visibility.actor_agent_ids)]
        elif visibility.mode == "pool":
            groups = None
        else:
            raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")
        if groups == []:
            return []
        adapter = self._adapter(native_handle)
        self._require_matching_index(adapter)
        found = asyncio.run(
            adapter.search(
                COLLECTION,
                query_vector=prepared_query["vector"],
                limit=prepared_query["limit"],
                with_vector=False,
                include_payload=True,
                node_name=groups,
                node_name_filter_operator="OR",
            )
        )
        return [
            {
                "event_id": item.payload.get("event_id"),
                "actor_agent_id": item.payload.get("actor_agent_id"),
                "chunk_id": str(item.id),
                "text": item.payload.get("text"),
                "distance": item.score,
                "source_revision": item.payload.get("source_revision"),
                "ranking": "vector distance to stored chunk text; no entity or relationship extraction",
            }
            for item in found
        ]
