"""Owner Service for sovereign memory operating over a Unix socket.

One framed JSON request per connection, with "op" one of: "capture" (store an
event and its pending receipts, then process it in the background),
"retrieve" (scoped retrieval across the tiers), "forget" (durably remove an
event the principal produced, then remove its projections) and "status" (an
event's durable receipts and whether every tier finished). The principal comes
from the transport, never from the request body.
"""

import asyncio
import json
import logging
import socket
import struct
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import duckdb
from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection
from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import connect as duckdb_connect

from service.memory_consolidation import MemoryConsolidator
from service.memory_dispatcher import SUCCESS_STATUSES, MemoryDispatcher, ProcessorRegistry
from service.memory_owner_core import SYSTEM_EVENT_PREFIX, OwnerCore
from service.memory_processor_seam import AuthenticatedPrincipal, MemoryRetriever, VisibilityPolicy
from service.memory_retrieval import MemoryRetrievalService

logger = logging.getLogger(__name__)
OPERATIONS = ("capture", "retrieve", "forget", "status")


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
    ):
        """Initialize the service with socket and db paths.

        registry holds the engines that process captured events and retrievers
        the engines that answer retrieval; both may be empty, in which case
        receipts stay pending and retrieval reports every tier unavailable. policy
        decides each principal's visibility; the default is restricted (own events only).
        max_request_bytes bounds one framed request and drain_timeout_seconds how
        long stop waits for open clients; both come from the deployment that
        starts the service, which reads them from its own declared configuration.
        """
        self.socket_path = Path(socket_path)
        self.db_path = Path(db_path)
        self.server = None
        self.authenticator = authenticator
        self.registry = registry or ProcessorRegistry()
        self.retrievers = retrievers or {}
        self.policy = policy
        self.max_request_bytes = max_request_bytes
        self.drain_timeout_seconds = drain_timeout_seconds

    async def start(self) -> None:
        """Initialize database connection and start UNIX socket server."""
        self.active_tasks = set()

        # Hold an exclusive single-process file ownership connection for the lifetime of the service.
        self.lifetime_conn = await duckdb_connect(str(self.db_path))
        core = OwnerCore(self.lifetime_conn)
        await core.initialize_schema()
        self.dispatcher = MemoryDispatcher(self.lifetime_conn, self.registry)
        self.retrieval = MemoryRetrievalService(self.lifetime_conn, self.retrievers, self.policy)
        self.processing_tasks: set[asyncio.Task] = set()
        # Restart: finish whatever the previous run left owed to registered engines.
        self._spawn_processing(self.dispatcher.process_pending())

        # Ensure socket directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        self.server = await asyncio.start_unix_server(
            self.handle_client_task, str(self.socket_path)
        )
        logger.info(f"Owner service listening on {self.socket_path}")

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

        # Drain active clients with a bounded timeout
        if hasattr(self, "active_tasks") and self.active_tasks:
            _, pending = await asyncio.wait(self.active_tasks, timeout=self.drain_timeout_seconds)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        # Let in-flight engine work finish: cancelling it mid-transaction would invalidate the connection.
        if getattr(self, "processing_tasks", None):
            await asyncio.gather(*self.processing_tasks, return_exceptions=True)

        if hasattr(self, "lifetime_conn") and self.lifetime_conn:
            await self.lifetime_conn.close()

        if self.socket_path.exists():
            self.socket_path.unlink()

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
        """
        try:
            length_bytes = await reader.readexactly(4)
            if not length_bytes:
                return

            payload_len = int.from_bytes(length_bytes, byteorder="big")
            if payload_len > self.max_request_bytes:
                self._send_error(
                    writer, "Payload Too Large", f"Exceeds {self.max_request_bytes} bytes"
                )
                return

            data = await reader.readexactly(payload_len)

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
                else:
                    response = await self._status(principal, payload)
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
        the insert reads the winner's row. Raises ValueError for an invalid or
        conflicting event.
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
        # The reply reports the durable receipts as captured; processing follows.
        self._spawn_processing(self.dispatcher.process_event(result_event["event_id"]))
        return {"status": "success", "event": result_event}

    async def _retrieve(self, principal: AuthenticatedPrincipal, request: dict) -> dict:
        """Answer a scoped retrieval; raises ValueError for a malformed query or limit."""
        query, limit = request.get("query"), request.get("limit")
        if not isinstance(query, str) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Invalid retrieval request: query and limit required")
        answers = await self.retrieval.retrieve(principal, query, limit)
        return {"status": "success", "tiers": answers}

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
