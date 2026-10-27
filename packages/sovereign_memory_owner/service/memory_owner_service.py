"""Owner Service for sovereign memory operating over a Unix socket.

One framed JSON request per connection, with "op" one of: "capture" (store an
event and its pending receipts, then process it in the background),
"retrieve" (scoped retrieval across the tiers), "forget" (durably remove an
event the principal produced, then remove its projections) and "status" (an
event's durable receipts and whether every tier finished). The review screen adds
"review_list", "review_show", "review_page", "review_judge" and "review_select",
"models" lists the model registry with each model's freshness, and "context" builds
the context block for a prompt and says what is waiting on numbers or a language model, "load" returns
one stored event with its original text, "model_parts" and "model" describe and run the
language-model memory steps. The principal comes from the transport, never from the request body.
"""

import asyncio
import hashlib
import json
import logging
import os
import socket
import stat
import struct
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

import duckdb
from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection
from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import connect as duckdb_connect
from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.search.search_config import SearchResults
from graphiti_core.search.search_helpers import search_results_to_context_string

from service.memory_combiners import (
    SCORES,
    ArtifactCombiner,
    ensure_starting_combiner,
    initialize_combiner_schema,
    judge_comparison,
    prepare_comparison,
    record_decision,
    register_combiner,
)
from service.memory_consolidation import MemoryConsolidator
from service.memory_dispatcher import SUCCESS_STATUSES, MemoryDispatcher, ProcessorRegistry
from service.memory_graphiti_episodes import _has_graphiti_schema
from service.memory_graphiti_search import keyword_search
from service.memory_ground_truth import (
    export_manifest,
    initialize_ground_truth_schema,
    record_case,
    verify_export,
)
from service.memory_model_parts import OPINIONS, DatabaseAccess, ModelParts
from service.memory_models import list_models, model_state
from service.memory_numbers import NumbersStore, initialize_numbers_schema, pending_requests
from service.memory_numbers_services import NumbersServices
from service.memory_owner_core import SYSTEM_EVENT_PREFIX, TIERS, OwnerCore
from service.memory_processor_seam import AuthenticatedPrincipal, MemoryRetriever, VisibilityPolicy
from service.memory_retrieval import MemoryRetrievalService
from service.memory_review import ReviewScreen
from service.memory_settings import (
    SearchSettings,
    initialize_settings_schema,
    record_settings,
)
from service.memory_supplier import NumbersSupplier, UnavailableSupplier
from service.memory_tier_support import group_of

logger = logging.getLogger(__name__)
OPERATIONS = (
    "capture",
    "retrieve",
    "forget",
    "status",
    "models",
    "context",
    "review_list",
    "review_show",
    "review_page",
    "review_judge",
    "review_select",
    "model_parts",
    "model",
    "load",
    "record_claim",
    "record_get",
)
SETTINGS_SOURCE = "memory_runtime_settings table, [ranking]"
SUPPLIER_ABSENT_REASON = (
    "no supplier is connected: agent_corpus has not built the door that hands saved numbers "
    "to another agent or the door for the numbers of a single search question"
)


def _same_file(first: Path, second: Path) -> bool:
    """Say whether two paths name one file: the same resolved path, or the same existing file."""
    if first.resolve() == second.resolve():
        return True
    return first.exists() and second.exists() and os.path.samefile(first, second)


def _inspect_socket_path(path: Path) -> tuple[int, int] | None:
    """Check that path is free or a stale socket and return the stale socket's identity.

    Returns None when nothing exists at path, and (device, inode) of a socket file
    nothing is listening on. Raises FileExistsError for a symlink, a regular file,
    a directory or any other non-socket, and for a socket another service answers
    on, and PermissionError for another user's socket. It deletes nothing.
    """
    try:
        found = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISSOCK(found.st_mode):
        raise FileExistsError(f"{path} exists and is not a socket; refusing to replace it")
    if found.st_uid != os.geteuid():
        raise PermissionError(f"{path} belongs to another user; refusing to replace it")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.setblocking(False)
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        return found.st_dev, found.st_ino
    except OSError as error:
        raise FileExistsError(f"Cannot tell whether the socket {path} is stale: {error}") from error
    finally:
        probe.close()
    raise FileExistsError(f"Another service is listening on {path}; refusing to replace it")


class _ServiceDatabase:
    """The service's database access for language-model parts: short reads and committed writes.

    It is for letting a part read and store on the memory file without holding a connection
    across a model call. It takes the running service. A read runs on the owner's own handle; a
    write runs on a connection of its own in one transaction that is committed when the work
    returns and rolled back when it raises. It never lets a model call happen inside either.
    """

    def __init__(self, service: "OwnerService") -> None:
        """Keep the service whose connection and transaction helper this class uses."""
        self.service = service

    @property
    def path(self) -> Path:
        """The path of the memory database file."""
        return self.service.db_path

    @property
    def numbers(self) -> NumbersServices | None:
        """The service's numbers services, through which a part owes numbers for a derived text."""
        return self.service.numbers

    async def read(self, work: Callable[[Any], Any]) -> Any:
        """Run work(handle) on the owner's worker and return its result."""
        conn = self.service.lifetime_conn
        return await conn.run(lambda: work(conn.native))

    async def write(self, work: Callable[[Any], Any]) -> Any:
        """Run work(handle) in one transaction on a connection of its own and commit it."""
        async with self.service._transaction_connection() as conn:
            await conn.begin()
            try:
                result = await conn.run(lambda: work(conn.native))
            except BaseException:
                await conn.rollback()
                raise
            await conn.commit()
            return result


class OwnerService:
    """The unified owner service running the DuckDB connection and socket server."""

    def __init__(
        self,
        socket_path: str | Path,
        db_path: str | Path,
        authenticator=None,
        registry: ProcessorRegistry | None = None,
        retrievers: dict[str, MemoryRetriever] | None = None,
        policy: VisibilityPolicy | None = None,
        *,
        max_request_bytes: int,
        drain_timeout_seconds: float,
        ranking: Mapping[str, Any] | None = None,
        supplier: NumbersSupplier | None = None,
        models: Sequence[Mapping[str, Any]] = (),
        artifacts: dict[str, ArtifactCombiner] | None = None,
        model_parts_factory: Callable[[DatabaseAccess], ModelParts] | None = None,
    ):
        """Initialize the service with socket and db paths.

        registry holds the engines that process captured events and retrievers
        the engines that answer retrieval; both may be empty, in which case
        receipts stay pending and retrieval reports every tier unavailable. policy
        decides each principal's visibility; the default is restricted (own events only).
        max_request_bytes bounds one framed request and drain_timeout_seconds how
        long stop waits for open clients; both come from the deployment that
        starts the service, which reads them from its own declared configuration.
        ranking is the declared table of the four search settings (validated here,
        so a missing value fails before anything starts; required whenever a tier
        ranks by models' numbers); supplier is where numbers come from, and when
        absent every request for numbers answers "numbers not yet supplied";
        models are registry rows to add at start; artifacts are trained
        combiners from the builder, keyed by combiner id; model_parts_factory
        makes the language-model router from the service's own database access
        when the service starts (absent, the "model" operations answer that no
        language model is configured).
        """
        self.socket_path = Path(socket_path)
        self.db_path = Path(db_path)
        if _same_file(self.socket_path, self.db_path):
            raise ValueError("socket_path and db_path name the same file")
        self.server = None
        self._socket_identity: tuple[int, int] | None = None
        self.authenticator = authenticator
        self.registry = registry or ProcessorRegistry()
        self.retrievers = retrievers or {}
        self.policy = policy
        self.max_request_bytes = max_request_bytes
        self.drain_timeout_seconds = drain_timeout_seconds
        self.active_tasks: set[asyncio.Task] = set()
        self.processing_tasks: set[asyncio.Task] = set()
        self.lifetime_conn: AsyncConnection | None = None
        engines = (
            *self.retrievers.values(),
            *(self.registry.get(t) for t in self.registry.tiers()),
        )
        if ranking is None and any(hasattr(engine, "attach_numbers") for engine in engines):
            raise ValueError(
                "A tier ranks by models' numbers, so the [ranking] settings are required"
            )
        self.settings: SearchSettings | None = (
            None if ranking is None else SearchSettings.from_mapping(ranking, tuple(SCORES))
        )
        self.supplier: NumbersSupplier = supplier or UnavailableSupplier(SUPPLIER_ABSENT_REASON)
        self.seed_models = tuple(models)
        self.artifacts = dict(artifacts or {})
        self.store: NumbersStore | None = None
        self.numbers: NumbersServices | None = None
        self._model_parts_factory = model_parts_factory
        self.model_parts: ModelParts | None = None

    async def start(self, *, process_pending: bool = True) -> None:
        """Initialize database connection and start UNIX socket server.

        If any step fails, the owner connection is released (after in-flight
        engine work finishes) before the error is raised again, so a failed
        start never leaves the database file locked. Before anything is acquired,
        the socket path must be free or a stale socket (FileExistsError otherwise:
        a regular file, symlink, directory or a socket another service answers on is
        never replaced). A stale socket is removed only once the database is owned
        and only if it is still the same file that was checked. This service's own
        socket is remembered by identity, so only it is removed at stop.
        process_pending=False is reserved for trusted maintenance that must not
        start ordinary engine preparation before it changes projection state.
        """
        stale = _inspect_socket_path(self.socket_path)
        listener: socket.socket | None = None
        try:
            # Hold an exclusive single-process file ownership connection for the lifetime of the service.
            self.lifetime_conn = await duckdb_connect(str(self.db_path))
            core = OwnerCore(self.lifetime_conn)
            await core.initialize_schema()
            await self._initialize_search_storage()
            self.dispatcher = MemoryDispatcher(self.lifetime_conn, self.registry, self.numbers)
            self.retrieval = MemoryRetrievalService(
                self.lifetime_conn,
                self.retrievers,
                self.policy,
                self.settings,
                self.artifacts,
                self.numbers,
            )
            self.review = ReviewScreen(self.lifetime_conn, self.retrieval.policy)
            if self._model_parts_factory is not None:
                self.model_parts = self._model_parts_factory(_ServiceDatabase(self))
            await self.dispatcher.initialize_storage()
            # Ensure socket directory exists
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            if stale is not None:
                if _inspect_socket_path(self.socket_path) != stale:
                    raise FileExistsError(f"{self.socket_path} changed while starting; refusing")
                self.socket_path.unlink()

            # Binding ourselves prevents asyncio's path-based server helper from
            # unlinking a socket that appeared after the stale-path check.
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.setblocking(False)
            listener.bind(str(self.socket_path))
            made = os.lstat(self.socket_path)
            self._socket_identity = (made.st_dev, made.st_ino)
            self.server = await asyncio.start_unix_server(self.handle_client_task, sock=listener)
            listener = None
            # Restart only after binding succeeds: failed startup must not start
            # model preparation or projection work.
            if process_pending:
                self._spawn_processing(self.dispatcher.process_pending())
        except BaseException:
            try:
                if listener is not None:
                    listener.close()
                if self.server is not None:
                    self.server.close()
                    await self.server.wait_closed()
                    self.server = None
                self._remove_own_socket()
            finally:
                await self._release_connection()
            raise
        logger.info(f"Owner service listening on {self.socket_path}")

    async def _initialize_search_storage(self) -> None:
        """Create the search tables, record the declared settings, add the configured models.

        The registry, pieces, requests, combiner and ground-truth tables are
        created if absent; the declared settings are appended to the parameter
        records when changed; each configured model becomes a registry row (an
        identical row already stored is left as it is); and the starting combiner
        is seeded once an embedding model exists. Called at start, on the owner's
        connection.
        """
        self.store = NumbersStore(self.lifetime_conn)
        self.numbers = NumbersServices(self.store, self.supplier)
        now = datetime.now(timezone.utc).isoformat()

        def work(handle) -> None:
            """Create tables and record the declared settings in one transaction."""
            initialize_numbers_schema(handle)
            initialize_combiner_schema(handle)
            initialize_ground_truth_schema(handle)
            initialize_settings_schema(handle)
            if self.settings is not None:
                record_settings(handle, self.settings, SETTINGS_SOURCE, now)

        await self.store.run_in_transaction(work)
        for model in self.seed_models:
            await self.store.register(**dict(model))
        await self.store.run_in_transaction(lambda handle: ensure_starting_combiner(handle, now))

    async def register_model(self, **fields: Any):
        """Add a model revision to the registry (a trusted deployment call) and seed the starting combiner.

        Fields are model_id, revision, width, kind, supplier_collection and
        supplier_recipe. Adding a model is adding a row; its numbers are received
        later. A model registered after the starting combiner exists joins no
        combiner until a candidate naming it wins on held-back cases.
        """
        row = await self.store.register(**fields)
        now = datetime.now(timezone.utc).isoformat()
        await self.store.run_in_transaction(lambda handle: ensure_starting_combiner(handle, now))
        return row

    async def sync_numbers(self) -> dict[str, Any]:
        """Compare each model's label with the supplier's and pull the numbers it can hand over.

        The deployment's scheduler calls this; the service runs no timer of its own.
        Where the supplier cannot answer, the numbers stay recorded as pending work.
        """
        return await self.store.sync(self.supplier)

    async def propose_combiner(self, **fields: Any):
        """Register a candidate combiner (it does not become current) and return its row."""
        fields.setdefault("proposed_at", datetime.now(timezone.utc).isoformat())
        return await self.store.run_in_transaction(
            lambda handle: register_combiner(handle, **fields)
        )

    async def evaluate_combiner(
        self, candidate_id: str, memory_kind: str | None = None
    ) -> dict[str, Any]:
        """Compare a candidate with the current combiner on held-back judged cases and record the decision.

        The candidate replaces the current combiner only if it scores strictly
        better and enough held-back cases exist; the decision and its reason are
        recorded either way and returned.
        """
        if self.settings is None:
            raise ValueError("Comparing combiners needs the declared search settings")
        now = datetime.now(timezone.utc).isoformat()
        settings = self.settings
        # An artifact combiner is asked asynchronously, so the cases are read first,
        # both combiners are scored without the database, and the decision row is
        # then appended in its own transaction.
        candidate, current, cases = await self.store.run_in_transaction(
            lambda handle: prepare_comparison(handle, candidate_id, memory_kind)
        )
        outcome = await judge_comparison(candidate, current, cases, settings, self.artifacts)
        return await self.store.run_in_transaction(
            lambda handle: record_decision(handle, candidate, current, outcome, settings, now)
        )

    async def export_ground_truth(self, purpose: str, created_by: str) -> dict[str, Any]:
        """Record and return an export manifest ("training" or "held_back_inputs") with its content."""
        now = datetime.now(timezone.utc).isoformat()
        return await self.store.run_in_transaction(
            lambda handle: export_manifest(handle, purpose, created_by, now)
        )

    async def verify_ground_truth_export(self, export_id: str) -> dict[str, Any]:
        """Say whether an earlier export's content is exactly as fingerprinted, and which cases lost their source."""
        return await self.store.run_in_transaction(lambda handle: verify_export(handle, export_id))

    def _remove_own_socket(self) -> None:
        """Remove the socket file only if it is the very file this service bound; safe to repeat.

        A path that is missing, or is now anything else (another service's socket,
        a regular file, a symlink), is left alone.
        """
        identity, self._socket_identity = self._socket_identity, None
        if identity is None:
            return
        try:
            found = os.lstat(self.socket_path)
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(found.st_mode) and (found.st_dev, found.st_ino) == identity:
            self.socket_path.unlink()

    async def _release_connection(self) -> None:
        """Let in-flight engine work finish within the drain bound, then close the connection.

        Work still running when drain_timeout_seconds ends is cancelled and awaited.
        A coroutine waiting on a model call stops at once; the call's own thread
        cannot be interrupted and is left to the provider's request timeout, and the
        receipt stays "processing", which the next start retries. A job already on
        the database worker is interrupted and drained by the connection itself, which
        rolls its uncommitted transaction back before the handle closes, so the
        connection is never closed under a surviving apply. An engine failure is
        logged by its own task callback.
        """
        if self.processing_tasks:
            _, stragglers = await asyncio.wait(
                self.processing_tasks, timeout=self.drain_timeout_seconds
            )
            for task in stragglers:
                task.cancel()
            if stragglers:
                await asyncio.gather(*stragglers, return_exceptions=True)
        connection, self.lifetime_conn = self.lifetime_conn, None
        if connection is not None:
            await connection.close()

    def _spawn_processing(self, coroutine) -> None:
        """Run processing in the background while keeping a reference until it ends."""
        task = asyncio.create_task(coroutine)
        self.processing_tasks.add(task)
        task.add_done_callback(self._processing_done)

    def _processing_done(self, task: asyncio.Task) -> None:
        """Forget a finished processing task and log, never swallow, its failure."""
        self.processing_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background processing failed", exc_info=task.exception())

    async def handle_client_task(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrapper to track active client tasks for draining during shutdown."""
        task = asyncio.create_task(self.handle_client(reader, writer))
        self.active_tasks.add(task)
        task.add_done_callback(self.active_tasks.discard)

    async def stop(self) -> None:
        """Stop the server, drain active clients, and clean up the socket."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        # Drain active clients with a bounded timeout
        if self.active_tasks:
            _, pending = await asyncio.wait(self.active_tasks, timeout=self.drain_timeout_seconds)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        await self._release_connection()

        self._remove_own_socket()

    async def serve_forever(self) -> None:
        """Run the server until cancelled."""
        if self.server:
            async with self.server:
                await self.server.serve_forever()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Answer one framed request on one connection and close it.

        Reads a 4-byte big-endian length, then that many bytes of JSON. Every
        refusal or failure is sent back as an error reply; none is reported as success.
        A client that closes before sending the whole request gets no reply and
        leaves no work behind; this is logged, not raised.
        """
        try:
            try:
                length_bytes = await reader.readexactly(4)
                if not length_bytes:
                    return
            except asyncio.IncompleteReadError:
                logger.warning("A client closed its connection before sending a whole request")
                return

            payload_len = int.from_bytes(length_bytes, byteorder="big")
            if payload_len > self.max_request_bytes:
                self._send_error(
                    writer, "Payload Too Large", f"Exceeds {self.max_request_bytes} bytes"
                )
                return

            try:
                data = await reader.readexactly(payload_len)
            except asyncio.IncompleteReadError:
                logger.warning("A client closed its connection before sending a whole request")
                return

            try:
                payload = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as e:
                self._send_error(writer, "Invalid JSON payload", str(e))
                return

            operation = payload.get("op", "capture")
            event_data = payload.get("event")
            payload_content_hex = payload.get("payload_content_hex")
            try:
                payload_content = (
                    bytes.fromhex(payload_content_hex) if payload_content_hex is not None else None
                )
            except ValueError as e:
                self._send_error(writer, "Malformed Hex Payload", str(e))
                return

            if operation not in OPERATIONS:
                self._send_error(writer, "Unsupported operation", str(operation))
                return
            if operation == "capture" and not event_data:
                self._send_error(writer, "Missing 'event' in payload", "")
                return

            principal = await self._authenticate(reader, writer)
            if principal is None:
                return

            try:
                if operation == "capture":
                    response = await self._capture(principal, event_data, payload_content)
                elif operation == "retrieve":
                    response = await self._retrieve(principal, payload)
                elif operation == "forget":
                    response = await self._forget(principal, payload)
                elif operation == "status":
                    response = await self._status(principal, payload)
                elif operation == "models":
                    response = await self._models()
                elif operation == "context":
                    response = await self._context(principal, payload)
                elif operation == "load":
                    response = await self._load(principal, payload)
                elif operation == "record_claim":
                    response = await self._record_claim(principal, payload)
                elif operation == "record_get":
                    response = await self._record_get(principal, payload)
                elif operation == "model_parts":
                    response = await self._model_parts_report()
                elif operation == "model":
                    response = await self._model(principal, payload)
                else:
                    response = await self._review(operation, principal, payload)
            except PermissionError as refusal:
                self._send_error(writer, "Visibility Refused", str(refusal))
                return
            except ValueError as ve:
                self._send_error(writer, "Validation Error", str(ve))
                return
            except (RuntimeError, TimeoutError, ConnectionError, duckdb.Error) as e:
                logger.exception("Operation %s failed", operation)
                self._send_error(writer, "Internal Error", f"{type(e).__name__}: {e}")
                return
            writer.write(json.dumps(response).encode("utf-8"))
            await writer.drain()

        finally:
            writer.close()
            await writer.wait_closed()

    @asynccontextmanager
    async def _transaction_connection(self) -> AsyncIterator[AsyncConnection]:
        """Yield a connection of its own for one multi-step transaction, closed afterwards.

        The lifetime connection runs engine work as single-job transactions;
        a transaction spanning several jobs on it could interleave with that
        work, so every capture (the client's or the owner's own) uses this.
        """
        conn = await duckdb_connect(str(self.db_path))
        try:
            yield conn
        finally:
            await conn.close()

    async def process_owed(self) -> dict[tuple[str, str], str]:
        """Retry every receipt still owed to a registered engine and return each pair's status.

        The deployment's scheduler calls this; the service runs no timer of its own.
        """
        return await self.dispatcher.process_pending()

    async def enforce_expiry(self) -> dict[str, dict[str, str]]:
        """Expire every legacy event whose ttl has elapsed through the registered engines.

        Returns expiry event id -> tier -> receipt status. Called by the
        deployment's scheduler; tiers without a processor stay pending.
        """
        async with self._transaction_connection() as conn:
            return await MemoryConsolidator(conn).enforce_scope_and_expiry(self.dispatcher)

    async def rebuild_projection(self, tier: str, request_id: str, max_items: int) -> dict:
        """Rebuild one embedding tier through a trusted deployment call, never a socket client.

        Expiry is durably captured first, then the dispatcher validates source recovery,
        archives receipts and resets/replays only the selected derived projection.
        """
        await self.enforce_expiry()
        return await self.dispatcher.rebuild(tier, request_id, max_items)

    async def _authenticate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> AuthenticatedPrincipal | None:
        """Return the transport-authenticated principal, or send an error and return None.

        Only the registered authenticator or the operating system's peer
        credentials name a principal; nothing in the request body does.
        """
        if self.authenticator:
            try:
                return await self.authenticator.authenticate_stream(reader, writer)
            except (RuntimeError, ValueError, ConnectionError) as e:
                self._send_error(
                    writer,
                    "Authentication Error",
                    f"Failed to authenticate connection via explicit authenticator: {e}",
                )
                return None
        # Attempt to extract OS peer credentials if supported (Linux / Unix sockets natively)
        peercred = writer.get_extra_info("peercred")
        transport_socket = writer.get_extra_info("socket")
        if peercred is None and transport_socket is not None and hasattr(socket, "SO_PEERCRED"):
            peercred = struct.unpack(
                "3i",
                transport_socket.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                ),
            )
        if not peercred:
            self._send_error(
                writer,
                "Configuration Error",
                "No authenticator registered and OS peercred unavailable",
            )
            return None
        # Without an explicit authenticator the uid is the only OS-guaranteed boundary.
        _, uid, _ = peercred
        return AuthenticatedPrincipal(f"os-uid-{uid}", "restricted")

    async def _capture(
        self, principal: AuthenticatedPrincipal, event_data: dict, payload_content: bytes | None
    ) -> dict:
        """Capture one event as the authenticated principal and start its processing.

        The principal replaces whatever actor the request claimed. Capture uses
        its own connection, so it never waits behind engine work. Two identical
        captures racing each other end with the same durable event: the loser of
        the insert reads the winner's row. The reply says "created": true for the
        call that wrote the event and false for a repeat of the identical event,
        so a caller can claim an event id exactly once. Raises ValueError for an
        invalid or conflicting event.
        """
        actor_field = (
            "agent_id" if event_data.get("schema_version") == "1.0.0" else "actor_agent_id"
        )
        event_data[actor_field] = principal.agent_id
        async with self._transaction_connection() as conn:
            core = OwnerCore(conn)
            try:
                result_event = await core.ingest_event(event_data, payload_content)
            except (duckdb.ConstraintException, duckdb.TransactionException):
                # Another request inserted the same event first; take the idempotent path.
                result_event = await core.ingest_event(event_data, payload_content)
            created = core.created_now
        # The reply reports the durable receipts as captured; processing follows.
        self._spawn_processing(self.dispatcher.process_event(result_event["event_id"]))
        return {"status": "success", "event": result_event, "created": created}

    async def _retrieve(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Answer a scoped retrieval; raises ValueError for a malformed query or limit."""
        query, limit = request.get("query"), request.get("limit")
        if not isinstance(query, str) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Invalid retrieval request: query and limit required")
        answers = await self.retrieval.retrieve(principal, query, limit)
        return {"status": "success", "tiers": answers}

    async def _context(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Answer "what do you know that matters for this" as a context block for a prompt.

        Goes in: a query and a limit, as for retrieve. Comes out: the context string
        built by Graphiti's own search_results_to_context_string from the episodes and
        messages the principal may see, followed by a WAITING section that says, per
        tier, what could not be answered and why ("waiting on numbers", "waiting on a
        language model" or "not configured"), plus the same lines as a list. Facts,
        entities and communities are always reported as waiting on a language model,
        because only Graphiti's extraction makes them. It never invents a result and
        never reports a tier that could not answer as an empty success. Raises
        ValueError for a malformed query or limit and PermissionError as retrieve does.
        """
        query, limit = request.get("query"), request.get("limit")
        if not isinstance(query, str) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Invalid context request: query and limit required")
        answers = await self.retrieval.retrieve(principal, query, limit)
        entries = [
            (tier, hit)
            for tier in ("temporal", "episodic")
            for hit in answers[tier].get("hits", [])
        ]
        ids = sorted({hit["event_id"] for _, hit in entries})

        def read() -> dict[str, str]:
            """Read each involved event's recorded time on the owner's worker."""
            rows = self.lifetime_conn.native.execute(
                "SELECT event_id, created_at FROM memory_events WHERE event_id IN (SELECT unnest(?))",
                [ids],
            ).fetchall()
            return dict(rows)

        created = await self.lifetime_conn.run(read)
        episodes = []
        for tier, hit in entries:
            stamp = datetime.fromisoformat(hit.get("valid_at") or created[hit["event_id"]])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            episodes.append(
                EpisodicNode(
                    name=hit["event_id"],
                    group_id=tier,
                    source=EpisodeType.text,
                    source_description=f"{tier} memory; {hit['ranking']}",
                    content=hit.get("content", hit.get("text", "")),
                    valid_at=stamp,
                )
            )
        groups = await self._allowed_groups(principal)

        def search_graph(handle: Any) -> tuple[SearchResults, int]:
            """Search the stored graph by words and count the entities it holds; reads only."""
            results = keyword_search(
                handle, None if groups is None else sorted(groups), query, limit
            )
            if not _has_graphiti_schema(handle):
                return results, 0
            held = handle.execute(
                "SELECT count(*) FROM graphiti.graph_node WHERE label = 'Entity'"
            ).fetchone()[0]
            return results, held

        graph, entity_count = await self.lifetime_conn.run(
            lambda: search_graph(self.lifetime_conn.native)
        )
        waiting = (
            []
            if entity_count
            else [
                "FACTS, ENTITIES and COMMUNITIES: waiting on a language model "
                "(Graphiti extracts them from episodes; none has been extracted yet)"
            ]
        )
        for tier, answer in answers.items():
            if answer["status"] == "ok":
                continue
            reason = answer.get("reason", answer["status"])
            left_out = answer.get("models", {}).get("left_out", {})
            if left_out:
                details = "; ".join(f"{model}: {why}" for model, why in left_out.items())
                waiting.append(f"{tier}: waiting on numbers ({details})")
            elif "no retriever registered" in reason:
                waiting.append(f"{tier}: not configured on this service")
            else:
                waiting.append(f"{tier}: {reason}")
        block = search_results_to_context_string(
            SearchResults(
                episodes=episodes,
                edges=graph.edges,
                nodes=graph.nodes,
                communities=graph.communities,
            )
        )
        lines = "\n".join(f"        - {line}" for line in waiting)
        return {
            "status": "success",
            "context": f"{block}    <WAITING>\n{lines}\n    </WAITING>\n",
            "waiting": waiting,
            "episodes": len(episodes),
        }

    async def _load(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Return one stored event the principal may see: its envelope and its original text.

        Goes in: an event_id. Comes out: found false for an unknown event; otherwise found true, the
        stored envelope (receipts as they stand) and payload_text, the original bytes decoded as
        UTF-8. Raises ValueError for a missing event_id or a payload that is not UTF-8 text, and
        PermissionError when the principal may not see the event. It changes nothing.
        """
        event_id = request.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("load needs an event_id")
        core = OwnerCore(self.lifetime_conn)
        try:
            event, payload = await core.load_event(event_id)
        except ValueError as unknown:
            if str(unknown).startswith("Unknown event"):
                return {"status": "success", "found": False}
            raise
        visibility = await self.retrieval.policy.resolve(principal)
        if not visibility.permits(
            event.get("actor_agent_id", event.get("agent_id")), principal.agent_id
        ):
            raise PermissionError("The event is not visible to this principal")
        try:
            text = (payload or b"").decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"The payload of {event_id} is not UTF-8 text: {error}") from error
        receipts = await core.receipts(event_id)
        return {
            "status": "success",
            "found": True,
            "event": {**event, "tier_receipts": receipts},
            "payload_text": text,
        }

    async def _record_claim(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Write a router bookkeeping record once and say whether this call wrote it.

        Goes in: key, kind, writer, content (all text) and optionally episode and episode_source.
        Comes out: created true for the first call with that key and false for every later one, with
        the record as stored in both cases. The record is not a memory and no tier processes it.
        Raises ValueError for a missing or non-text field.
        """
        fields = {name: request.get(name) for name in ("key", "kind", "writer", "content")}
        optional = {name: request.get(name) for name in ("episode", "episode_source")}
        if any(not isinstance(value, str) or not value for value in fields.values()) or any(
            value is not None and not isinstance(value, str) for value in optional.values()
        ):
            raise ValueError("record_claim needs key, kind, writer and content as text")
        async with self._transaction_connection() as conn:
            created, record = await OwnerCore(conn).claim_record(
                principal.agent_id,
                fields["key"],
                fields["kind"],
                fields["writer"],
                fields["content"],
                optional["episode"],
                optional["episode_source"],
                datetime.now(timezone.utc).isoformat(),
            )
        return {"status": "success", "created": created, "record": record}

    async def _record_get(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Return one router bookkeeping record of the principal, or found false. Reads only."""
        key = request.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("record_get needs a key")
        record = await OwnerCore(self.lifetime_conn).get_record(principal.agent_id, key)
        return {"status": "success", "found": record is not None, "record": record}

    async def _model_parts_report(self) -> dict:
        """Say which language model each part asks for and which model last answered it.

        Goes in: nothing. Comes out: the main and second-opinion model names from the
        settings, where the server holds the main model (processor only or graphics card), and
        per part its steps and the model its latest answer came from. It calls no model. Raises
        ValueError when the service was started with no language model configured.
        """
        if self.model_parts is None:
            raise ValueError("no language model is configured on this service")
        return {"status": "success", **await self.model_parts.describe()}

    async def _allowed_groups(self, principal: AuthenticatedPrincipal) -> set[str] | None:
        """Return the Graphiti group ids the principal may see, or None when it may see every group."""
        visibility = await self.retrieval.policy.resolve(principal)
        if visibility.mode == "pool":
            return None
        actors = (
            {principal.agent_id}
            if visibility.mode == "own"
            else set(visibility.actor_agent_ids) | {principal.agent_id}
        )
        return {group_of(actor) for actor in actors}

    async def _model(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Run one named step of one part with the main or the second-opinion model.

        Goes in: part, step, args (an object) and opinion ("main" by default or "second"). Every
        event the args name must be visible to the principal and a group id must be one the
        principal may see. Comes out: the step's real output plus part, step, opinion,
        requested_model, achieved_model, placement and seconds. Raises ValueError for a
        malformed request or an unknown part, step or opinion, PermissionError for an event or
        group the principal may not see, and RuntimeError (naming the cause) when the model
        call itself fails; nothing is stored when a step fails.
        """
        if self.model_parts is None:
            raise ValueError("no language model is configured on this service")
        part, step = request.get("part"), request.get("step")
        args, opinion = request.get("args", {}), request.get("opinion", "main")
        if not isinstance(part, str) or not isinstance(step, str) or not isinstance(args, dict):
            raise ValueError("model needs part and step (text) and args (an object)")
        if opinion not in OPINIONS:
            raise ValueError(f"opinion must be one of {', '.join(OPINIONS)}")
        visibility = await self.retrieval.policy.resolve(principal)
        core = OwnerCore(self.lifetime_conn)
        for event_id in args.get("event_ids", []):
            event, _ = await core.load_event(event_id)
            if not visibility.permits(
                event.get("actor_agent_id", event.get("agent_id")), principal.agent_id
            ):
                raise PermissionError(f"The event {event_id} is not visible to this principal")
        groups = await self._allowed_groups(principal)
        for group_key in ("group_id", "graph_group_id"):
            if (
                part in ("graphiti", "zep")
                and group_key in args
                and groups is not None
                and args[group_key] not in groups
            ):
                raise PermissionError("That group is not visible to this principal")
        actors = (
            None
            if visibility.mode == "pool"
            else {principal.agent_id}
            | (set(visibility.actor_agent_ids) if visibility.mode == "granted" else set())
        )
        owner_keys = ("agent_id", "foreground_agent_id") + (("user_id",) if part == "mem0" else ())
        for key in owner_keys:
            if key in args and actors is not None and args[key] not in actors:
                raise PermissionError(f"The {key} {args[key]!r} is not visible to this principal")
        try:
            result = await self.model_parts.run(part, step, args, opinion)
        except (ValueError, PermissionError):
            raise
        except Exception as error:
            raise RuntimeError(f"{type(error).__name__}: {error}") from error
        return {"status": "success", **result}

    async def _models(self) -> dict:
        """Report every registered model with its freshness and reasons, and the numbers still owed.

        It names no event and shows no event text, so it needs no visibility check
        beyond authentication. A stale model is reported stale with why.
        """

        def read(handle):
            """Measure every model and list the owed numbers on the owner's worker."""
            states = []
            for model in list_models(handle):
                state = model_state(handle, model, "event")
                derived = model_state(handle, model, "derived")
                states.append(
                    {
                        "model_id": model.model_id,
                        "revision": model.revision,
                        "width": model.width,
                        "kind": model.kind,
                        "supplier_collection": model.supplier_collection,
                        "supplier_recipe": model.supplier_recipe,
                        "registered_at": model.registered_at,
                        "numbers_table": model.numbers_table,
                        "fresh": state.fresh,
                        "reasons": list(state.reasons),
                        "rows": state.rows,
                        "stale_rows": state.stale_rows,
                        "missing_pieces": state.missing_pieces,
                        "derived_fresh": derived.fresh,
                        "derived_reasons": list(derived.reasons),
                        "derived_rows": derived.rows,
                        "derived_missing": derived.missing_pieces,
                    }
                )
            owed = [
                {k: v for k, v in request.items() if k != "text"}
                for request in pending_requests(handle)
            ]
            return states, owed

        states, owed = await self.lifetime_conn.run(lambda: read(self.lifetime_conn.native))
        return {"status": "success", "models": states, "numbers_owed": owed}

    async def _review(
        self, operation: str, principal: AuthenticatedPrincipal, request: dict
    ) -> dict:
        """Serve the review screen: list, show, page, judge and select.

        Every call is limited to cases whose events the principal may see and
        that are not removed; a refused case raises PermissionError. Raises
        ValueError for a malformed request.
        """
        if operation == "review_list":
            return {"status": "success", "cases": await self.review.list_cases(principal)}
        if operation == "review_select":
            return await self._review_select(principal, request)
        case_id = request.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("A review request needs a case_id")
        if operation in ("review_show", "review_page"):
            view = await self.review.show(principal, case_id)
            if operation == "review_page":
                return {"status": "success", "html": ReviewScreen.render(view)}
            return {"status": "success", "view": view}
        chosen = request.get("chosen_items", [])
        if not isinstance(chosen, list) or not all(isinstance(item, str) for item in chosen):
            raise ValueError("chosen_items must be a list of item ids")
        judgment_id = await self.review.judge(
            principal,
            case_id,
            verdict=request.get("verdict", ""),
            chosen_items=chosen,
            typed_answer=request.get("typed_answer"),
            note=request.get("note"),
        )
        return {"status": "success", "judgment_id": judgment_id}

    async def _review_select(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Queue a case the principal chooses to judge; it points to events and copies no event text.

        The question and the events must be given, every event must be live and
        visible to the principal, and the case id derives from its content so
        asking again queues one case. Raises PermissionError when an event is not
        visible and ValueError for a malformed request or when the deployment
        declared no [ranking] settings (the held-back share comes from them).
        """
        question, events = request.get("question"), request.get("events")
        memory_kind = request.get("memory_kind")
        if (
            not isinstance(question, str)
            or not question
            or not isinstance(events, list)
            or not events
            or not all(isinstance(event, str) for event in events)
            or memory_kind not in TIERS
        ):
            raise ValueError("review_select needs question, events and a memory_kind")
        if self.settings is None:
            raise ValueError(
                "review_select needs the declared [ranking] settings (the held-back share), "
                "and this deployment declared none"
            )
        visibility = await self.retrieval.policy.resolve(principal)
        producers = await OwnerCore(self.lifetime_conn).live_producers(events)
        if not all(
            event in producers and visibility.permits(producers[event], principal.agent_id)
            for event in events
        ):
            raise PermissionError("An event of the case is not visible to this principal")
        digest = hashlib.sha256(
            json.dumps([principal.agent_id, memory_kind, question, sorted(set(events))]).encode()
        ).hexdigest()
        case_id = f"case-{digest}"
        now = datetime.now(timezone.utc).isoformat()
        await self.store.run_in_transaction(
            lambda handle: record_case(
                handle,
                case_id=case_id,
                kind="retrieved_item",
                memory_kind=memory_kind,
                input_kind="question",
                input_value=question,
                events=events,
                created_at=now,
                created_by=principal.agent_id,
                held_back_share=self.settings.held_back_share,
                selected_by_owner=True,
            )
        )
        return {"status": "success", "case_id": case_id}

    async def _forget(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Forget one captured event the principal produced, through every registered engine.

        The removal is durable and withheld from retrieval when this returns;
        "complete" is true only once every tier's receipt reports a processed
        removal. Raises PermissionError when the principal did not produce the
        event (only its producer may forget it until a permission owner
        supplies broader grants) and ValueError for an unknown target.
        """
        target_id = request.get("event_id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("forget needs the event_id to forget")
        async with self._transaction_connection() as conn:
            core = OwnerCore(conn)
            target, _ = await core.load_event(target_id)
            if target.get("actor_agent_id", target.get("agent_id")) != principal.agent_id:
                raise PermissionError("Only the producer of an event may forget it")
            removal_id = await core.ingest_system_removal(
                target_id,
                "forget",
                requested_by=principal.agent_id,
                requested_at=datetime.now(timezone.utc).isoformat(),
                details={"requested_by": principal.agent_id, "reason": "forgotten by its producer"},
            )
            status = await self._status_of(core, removal_id)
        self._spawn_processing(self.dispatcher.process_event(removal_id))
        return status

    async def _status(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Report the receipts of an event the principal may see.

        For an owner-authored removal the principal must be allowed to see the
        removed event. Raises PermissionError otherwise, and ValueError for an
        unknown event.
        """
        event_id = request.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("status needs an event_id")
        core = OwnerCore(self.lifetime_conn)
        event, _ = await core.load_event(event_id)
        subject = event
        if str(event.get("event_type", "")).startswith(SYSTEM_EVENT_PREFIX):
            subject, _ = await core.load_event(event["causation_event_id"])
        visibility = await self.retrieval.policy.resolve(principal)
        if not visibility.permits(
            subject.get("actor_agent_id", subject.get("agent_id")), principal.agent_id
        ):
            raise PermissionError("The event is not visible to this principal")
        return await self._status_of(core, event_id)

    @staticmethod
    async def _status_of(core: OwnerCore, event_id: str) -> dict:
        """Return an event's durable receipts, its removal and whether every tier processed it."""
        receipts = await core.receipts(event_id)
        return {
            "status": "success",
            "event_id": event_id,
            "removed_by": await core.removal_of(event_id),
            "receipts": receipts,
            "complete": bool(receipts)
            and all(r["status"] in SUCCESS_STATUSES for r in receipts.values()),
        }

    def _send_error(self, writer: asyncio.StreamWriter, message: str, details: str) -> None:
        """Send an error response."""
        response = {"status": "error", "message": message, "details": details}
        writer.write(json.dumps(response).encode("utf-8"))
        # We don't await drain here as we are about to close in the finally block anyway
