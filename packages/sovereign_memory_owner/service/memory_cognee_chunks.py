"""Cognee text chunks as the owner's relational tier: one piece, one chunk.

The unit this tier stores is the piece the supplier cut (agent_corpus): its id,
its text, its position in its source event and its event id. Each piece becomes
one Cognee DocumentChunk graph node (through Cognee's DuckDB graph adapter) that
carries its event provenance, written on the owner's borrowed native transaction
handle so it commits with the receipt. The chunk id derives from the piece id,
so a replay overwrites the same chunk. This tier never cuts text and never makes
numbers: Cognee's own cutter is switched off on this path (NoCutChunker refuses
to cut), and when an event has no pieces yet the tier raises
NumbersNotYetSupplied, which the owner records as retryable pending work.

The numbers live in the per-model numbers tables, one per registered model, and
a search ranks the pieces once per fresh model from that model's own numbers,
limited in SQL to the producers the asker may see. Every hit names its event, so
the owner's live, not removed, not expired and visible checks still decide
whether it may be shown. Cognee's cognify (entity and relationship extraction)
needs a language model and is not run: no entity, relationship or summary exists.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import duckdb
from cognee.modules.chunking.Chunker import Chunker
from cognee_community_hybrid_adapter_duckdb.duckdb_adapter import DuckDBAdapter

from service.memory_models import ModelRow
from service.memory_numbers import pieces_of_event
from service.memory_numbers_services import NumbersServices, ReceivedNumbersEmbeddingEngine
from service.memory_processor_seam import (
    OwnerReader,
    ProcessorResult,
    RetrievalScope,
    TierRankings,
)
from service.memory_supplier import PENDING_WORK, NumbersNotYetSupplied
from service.memory_tier_support import (
    actor_of,
    event_instant,
    group_of,
    is_removal,
    removal_target,
)

TIER_NAME = "relational"
PROCESSOR_ID = "cognee-duckdb-chunks"
PROJECTION_VERSION = "cognee-piece-chunk-1"
# The vector collection the earlier one-event-one-chunk tier wrote; only a rebuild reads it, to retire it.
LEGACY_VECTOR_COLLECTION = "DocumentChunk_text"
NODE_TYPE = "DocumentChunk"
CHUNK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "sovereign-memory-owner/cognee-chunk")
PROVENANCE_FIELDS = (
    "schema_version",
    "source_revision",
    "source_artifact_id",
    "work_id",
    "payload_sha256",
    "exactness",
)


class NoCutChunker(Chunker):
    """Cognee's chunker slot on the memory path: it refuses to cut anything.

    Goes in: a Cognee document, as for any chunker. Comes out: nothing; read
    raises RuntimeError. Pass it as the chunker wherever Cognee's pipeline could
    reach this memory so that Cognee's own cutter can never split text here:
    cutting belongs to the supplier. It never reads or splits the document.
    """

    chunker_id = "memory-no-cut"

    def read(self):
        """Refuse: pieces come from the supplier, never from Cognee's cutter."""
        raise RuntimeError(
            "Cognee's own cutter is switched off on the memory path; "
            "pieces are cut by the supplier and received"
        )


def chunk_id(piece_id: str) -> str:
    """Return the deterministic Cognee chunk id of a supplied piece; pure."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, piece_id))


def legacy_chunk_id(event_id: str) -> str:
    """Return the chunk id the earlier one-event-one-chunk tier gave an event; pure.

    Used only to recognise and retire chunks written before pieces existed.
    """
    return str(uuid.uuid5(CHUNK_NAMESPACE, event_id))


@dataclass(frozen=True)
class PreparedChunk:
    """One chunk to store: its id and the node properties. Plain data, never a handle."""

    chunk_id: str
    properties: dict[str, Any]


@dataclass(frozen=True)
class PreparedChunks:
    """The chunks of one event's pieces to store, or (removal_target set) the chunks to delete.

    chunks lists the chunks to store; delete_ids names every chunk id a removal
    deletes. Holds plain data only, never a database handle.
    """

    chunks: tuple[PreparedChunk, ...] = ()
    removal_target: str | None = None
    delete_ids: tuple[str, ...] = ()
    embedding_engine: Any = None


class CogneeChunkTier:
    """Owner processor and retriever for the relational tier, backed by piece chunks.

    database_name is the DuckDB schema Cognee's tables live in. embedding_model,
    when given as (model id, revision), binds the embedder object Cognee's adapter
    receives to that registered model; it answers from received numbers only. The
    tier never opens, commits, rolls back or closes a connection, makes a number
    or cuts text.
    """

    tier_name = TIER_NAME

    def __init__(
        self, database_name: str, *, embedding_model: tuple[str, str] | None = None
    ) -> None:
        """Keep the schema name and the optional embedder binding; nothing is opened yet."""
        if not isinstance(database_name, str) or not database_name:
            raise ValueError("CogneeChunkTier needs a non-empty database_name")
        self.database_name = database_name
        self.embedding_model = embedding_model
        self._reader: OwnerReader | None = None
        self._services: NumbersServices | None = None

    def attach_reader(self, reader: OwnerReader) -> None:
        """Receive the owner's reader; every read before a decision goes through it."""
        self._reader = reader

    def attach_numbers(self, services: NumbersServices) -> None:
        """Receive the owner's numbers services (lookup, owed requests, question door)."""
        self._services = services

    def model_labels(self) -> list[str]:
        """Return the model labels a rebuild of this tier covers: none, as it holds no numbers."""
        return []

    def _need_reader(self) -> OwnerReader:
        """Return the attached reader or raise RuntimeError if the owner never registered the tier."""
        if self._reader is None:
            raise RuntimeError("CogneeChunkTier has no owner reader attached")
        return self._reader

    def _need_services(self) -> NumbersServices:
        """Return the numbers services or raise RuntimeError if the owner never attached them."""
        if self._services is None:
            raise RuntimeError("CogneeChunkTier has no numbers services attached")
        return self._services

    def _adapter(
        self, handle: duckdb.DuckDBPyConnection, engine: Any = None
    ) -> DuckDBAdapter:
        """Build Cognee's adapter on the owner's handle for one call; it borrows, never owns."""
        return DuckDBAdapter(
            connection=handle, database_name=self.database_name, embedding_engine=engine
        )

    def reset_projection(self, handle: duckdb.DuckDBPyConnection, max_items: int) -> None:
        """Delete only source-backed Owner chunks, preserving other Cognee graph records.

        Every chunk node with Owner provenance (a piece chunk, or a chunk the
        earlier one-event-one-chunk tier wrote) must match a captured event and
        its recorded producer before it is deleted; the earlier tier's vector
        collection, if present, is checked the same way and dropped. Unknown data
        or excessive rows refuse the reset. The caller owns the transaction; this
        method never commits and never calls a model.
        """
        adapter = self._adapter(handle)
        asyncio.run(adapter.initialize())

        def source_of(event_id: str | None):
            """Return the producer recorded for an event this tier owes, or None."""
            return handle.execute(
                "SELECT e.actor_agent_id FROM memory_events e JOIN memory_receipts r "
                "USING (event_id) WHERE e.event_id = ? AND r.tier = ? AND "
                "(e.schema_version LIKE '2.%' OR json_extract_string(e.raw_event, '$.tier') = "
                "r.tier)",
                [event_id, TIER_NAME],
            ).fetchone()

        table = adapter._table(LEGACY_VECTOR_COLLECTION)
        exists = handle.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [self.database_name, LEGACY_VECTOR_COLLECTION],
        ).fetchone()
        if exists:
            if handle.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > max_items:
                raise ValueError("Cognee projection exceeds the requested rebuild item limit")
            for identifier, payload in handle.execute(
                f"SELECT id, payload FROM {table}"
            ).fetchall():
                data = json.loads(payload or "{}")
                event_id = data.get("event_id")
                source = source_of(event_id)
                if (
                    source is None
                    or source[0] != data.get("actor_agent_id")
                    or identifier != legacy_chunk_id(event_id)
                ):
                    raise ValueError("Cognee collection contains a chunk without Owner provenance")
        nodes = handle.execute(
            f"SELECT id, properties FROM {adapter._node_table} "
            "WHERE type = ? AND json_extract_string(properties, '$.event_id') IS NOT NULL "
            "LIMIT ?",
            [NODE_TYPE, max_items + 1],
        ).fetchall()
        if len(nodes) > max_items:
            raise ValueError("Cognee graph projection exceeds the requested rebuild item limit")
        identifiers = []
        for identifier, payload in nodes:
            data = json.loads(payload)
            event_id, piece_id = data.get("event_id"), data.get("piece_id")
            expected = chunk_id(piece_id) if piece_id else legacy_chunk_id(event_id)
            if identifier != expected:
                raise ValueError("Cognee graph contains ambiguous Owner chunk provenance")
            if source_of(event_id) is None:
                raise ValueError("Cognee graph chunk has no recoverable Owner source")
            identifiers.append(identifier)
        asyncio.run(adapter.delete_nodes(identifiers))
        if exists:
            handle.execute(f"DROP TABLE {table}")

    # ---- write seam -------------------------------------------------------------

    async def prepare(
        self, event_envelope: dict[str, Any], verified_payload_bytes: bytes | None
    ) -> PreparedChunks:
        """Read the event's supplied pieces (or name the chunks a removal deletes); no model.

        Raises NumbersNotYetSupplied, naming the pending work, when the event has
        no pieces yet; the owner records that as a retryable failure and the
        owed pieces are recorded for the supplier. Raises TerminalProcessingError
        for a removal that does not match its causation event.
        """
        reader, services = self._need_reader(), self._need_services()
        if is_removal(event_envelope):
            target, _ = removal_target(event_envelope, verified_payload_bytes)
            pieces = await reader(lambda handle: pieces_of_event(handle, target))
            ids = tuple(chunk_id(p["piece_id"]) for p in pieces) + (legacy_chunk_id(target),)
            return PreparedChunks(removal_target=target, delete_ids=ids)
        event_id = event_envelope["event_id"]
        actor = actor_of(event_envelope)
        pieces = await reader(lambda handle: pieces_of_event(handle, event_id))
        if not pieces:
            await services.request_pieces(event_id, f"{PENDING_WORK}: no pieces yet")
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: the supplier has not yet handed over the pieces of {event_id}"
            )
        engine = await self._engine(services)
        provenance = {key: event_envelope.get(key) for key in PROVENANCE_FIELDS}
        chunks = tuple(
            PreparedChunk(
                chunk_id(piece["piece_id"]),
                {
                    "name": piece["piece_id"],
                    "type": NODE_TYPE,
                    "text": piece["text"],
                    "belongs_to_set": [group_of(actor)],
                    "event_id": event_id,
                    "piece_id": piece["piece_id"],
                    "position": piece["position"],
                    "text_fingerprint": piece["text_fingerprint"],
                    "actor_agent_id": actor,
                    "event_time": event_instant(event_envelope),
                    "event_type": event_envelope.get(
                        "event_type", event_envelope.get("content_type")
                    ),
                    **provenance,
                },
            )
            for piece in pieces
        )
        return PreparedChunks(chunks=chunks, embedding_engine=engine)

    async def _engine(self, services: NumbersServices) -> ReceivedNumbersEmbeddingEngine | None:
        """Build the received-numbers embedder object for the configured model, if one is bound."""
        if self.embedding_model is None:
            return None
        model: ModelRow = await services.model(*self.embedding_model)
        return ReceivedNumbersEmbeddingEngine(services, model)

    def apply(
        self, native_tx_handle: duckdb.DuckDBPyConnection, prepared_work: PreparedChunks
    ) -> ProcessorResult:
        """Store or delete the event's chunk nodes on the owner's open transaction.

        A removal of chunks that were never stored reports processed_no_new_fact.
        """
        adapter = self._adapter(native_tx_handle, prepared_work.embedding_engine)
        asyncio.run(adapter.initialize())
        if prepared_work.removal_target is not None:
            existed = any(
                asyncio.run(adapter.get_node(identifier)) is not None
                for identifier in prepared_work.delete_ids
            )
            asyncio.run(adapter.delete_nodes(list(prepared_work.delete_ids)))
            status = "processed" if existed else "processed_no_new_fact"
        else:
            asyncio.run(
                adapter.add_nodes([(c.chunk_id, c.properties) for c in prepared_work.chunks])
            )
            status = "processed"
        return ProcessorResult(status, PROCESSOR_ID, version("cognee"), PROJECTION_VERSION)

    # ---- read seam --------------------------------------------------------------

    async def prepare_query(self, scope: RetrievalScope, query: str, limit: int) -> dict[str, Any]:
        """Fetch the question's numbers, per fresh model, from the supplier's door.

        A model that is stale, or whose question numbers the supplier cannot give,
        is left out with its reason; it is never counted as an empty ranking.
        Memory never embeds the question itself.
        """
        services = self._need_services()
        fresh, left_out = await services.fresh_models()
        vectors: dict[str, tuple[ModelRow, list[float]]] = {}
        for model in fresh:
            try:
                vectors[model.label] = (
                    model,
                    await services.question_numbers(model.model_id, model.revision, query),
                )
            except NumbersNotYetSupplied as pending:
                left_out[model.label] = f"question numbers not supplied: {pending}"
        return {"vectors": vectors, "left_out": left_out, "limit": limit}

    def retrieve(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: dict[str, Any],
    ) -> TierRankings:
        """Rank the pieces once per model from that model's own numbers, for permitted producers.

        Each ranking is the nearest pieces by cosine distance (lower is nearer)
        in that model's numbers table, limited in SQL to the producers the
        visibility names and to events not removed. Raises ValueError for an
        unknown visibility mode. It never writes and never ranks across models.
        """
        visibility = scope.visibility
        if visibility.mode == "own":
            condition, params = "e.actor_agent_id = ?", [scope.agent_id]
        elif visibility.mode == "granted":
            actors = sorted(visibility.actor_agent_ids)
            if not actors:
                return TierRankings({}, dict(prepared_query["left_out"]))
            condition, params = "e.actor_agent_id IN (SELECT unnest(?))", [actors]
        elif visibility.mode == "pool":
            condition, params = "TRUE", []
        else:
            raise ValueError(f"Unsupported visibility mode: {visibility.mode!r}")
        rankings: dict[str, list[dict[str, Any]]] = {}
        for label, (model, vector) in prepared_query["vectors"].items():
            rows = native_handle.execute(
                f"SELECT n.piece_id, n.event_id, p.text, p.position, e.actor_agent_id, "
                f"json_extract_string(e.raw_event, '$.source_revision'), "
                f"array_cosine_distance(n.numbers, CAST(? AS FLOAT[{model.width}])) AS distance "
                f"FROM {model.numbers_table} n JOIN memory_pieces p ON p.piece_id = n.piece_id "
                "JOIN memory_events e ON e.event_id = n.event_id "
                "LEFT JOIN memory_removals r ON r.target_event_id = n.event_id "
                f"WHERE NOT n.stale AND p.source = 'event' AND r.target_event_id IS NULL "
                f"AND {condition} ORDER BY distance, n.piece_id LIMIT ?",
                [vector, *params, prepared_query["limit"]],
            ).fetchall()
            rankings[label] = [
                {
                    "event_id": row[1],
                    "item_id": row[0],
                    "piece_id": row[0],
                    "chunk_id": chunk_id(row[0]),
                    "position": row[3],
                    "text": row[2],
                    "actor_agent_id": row[4],
                    "source_revision": row[5],
                    "distance": row[6],
                    "ranking": (
                        f"cosine distance of {label}'s own numbers; "
                        "no entity or relationship extraction"
                    ),
                }
                for row in rows
            ]
        return TierRankings(rankings, dict(prepared_query["left_out"]))

