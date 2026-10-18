"""Owner Service for sovereign memory operating over a Unix socket."""

import asyncio
import json
import logging
import socket
import struct
from pathlib import Path

from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import connect as duckdb_connect

from service.memory_dispatcher import MemoryDispatcher, ProcessorRegistry
from service.memory_owner_core import OwnerCore
from service.memory_processor_seam import AuthenticatedPrincipal, MemoryRetriever, VisibilityPolicy
from service.memory_retrieval import MemoryRetrievalService

logger = logging.getLogger(__name__)


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
    ):
        """Initialize the service with socket and db paths.

        registry holds the engines that process captured events and retrievers
        the engines that answer retrieval; both may be empty, in which case
        receipts stay pending and retrieval reports every tier unavailable. policy
        decides each principal's visibility; the default is restricted (own events only).
        """
        self.socket_path = Path(socket_path)
        self.db_path = Path(db_path)
        self.server = None
        self.authenticator = authenticator
        self.registry = registry or ProcessorRegistry()
        self.retrievers = retrievers or {}
        self.policy = policy

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
            _, pending = await asyncio.wait(self.active_tasks, timeout=5.0)
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
        """Handle incoming UNIX socket connections concurrently using independent db connections.

        Reads 4 bytes big-endian framing length to receive entire payload securely.
        """
        conn = None
        try:
            length_bytes = await reader.readexactly(4)
            if not length_bytes:
                return

            payload_len = int.from_bytes(length_bytes, byteorder="big")
            if payload_len > 10 * 1024 * 1024:  # 10MB limit
                self._send_error(writer, "Payload Too Large", f"Exceeds 10MB bounds")
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

            if operation not in ("capture", "retrieve"):
                self._send_error(writer, "Unsupported operation", str(operation))
                return
            if operation == "capture" and not event_data:
                self._send_error(writer, "Missing 'event' in payload", "")
                return

            # 1. Enforce transport-layer authentication rather than trusting JSON actor claims
            if self.authenticator:
                try:
                    principal = await self.authenticator.authenticate_stream(reader, writer)
                except (RuntimeError, ValueError, ConnectionError) as e:
                    self._send_error(
                        writer,
                        "Authentication Error",
                        f"Failed to authenticate connection via explicit authenticator: {e}",
                    )
                    return
            else:
                # Attempt to extract OS peer credentials if supported (Linux / Unix sockets natively)
                peercred = writer.get_extra_info("peercred")
                transport_socket = writer.get_extra_info("socket")
                if (
                    peercred is None
                    and transport_socket is not None
                    and hasattr(socket, "SO_PEERCRED")
                ):
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
                    return
                # Extract pid, uid, gid. Use uid as fallback actor boundary.
                # In a real environment, uid might map to a user agent, but without an explicit auth module,
                # uid is the only OS-guaranteed boundary available natively.
                _, uid, _ = peercred
                principal = AuthenticatedPrincipal(f"os-uid-{uid}", "restricted")

            if operation == "retrieve":
                query, limit = payload.get("query"), payload.get("limit")
                if not isinstance(query, str) or not isinstance(limit, int) or limit < 1:
                    self._send_error(
                        writer, "Invalid retrieval request", "query and limit required"
                    )
                    return
                try:
                    answers = await self.retrieval.retrieve(principal, query, limit)
                except PermissionError as refusal:
                    self._send_error(writer, "Visibility Refused", str(refusal))
                    return
                writer.write(json.dumps({"status": "success", "tiers": answers}).encode("utf-8"))
                await writer.drain()
                return

            # Bind the authenticated principal to the event explicitly
            actor_field = (
                "agent_id" if event_data.get("schema_version") == "1.0.0" else "actor_agent_id"
            )
            event_data[actor_field] = principal.agent_id

            try:
                # Instantiate per-request independent connection per the concurrency rule
                conn = await duckdb_connect(str(self.db_path))
                core = OwnerCore(conn)
                result_event = await core.ingest_event(event_data, payload_content)

                response = {"status": "success", "event": result_event}
                writer.write(json.dumps(response).encode("utf-8"))
                await writer.drain()
                # The reply above reports the durable receipts as captured; processing follows.
                self._spawn_processing(self.dispatcher.process_event(result_event["event_id"]))
            except ValueError as ve:
                self._send_error(writer, "Validation Error", str(ve))
            except (RuntimeError, TimeoutError, ConnectionError) as e:
                self._send_error(writer, "Internal Error", str(e))

        finally:
            if conn:
                await conn.close()
            writer.close()
            await writer.wait_closed()

    def _send_error(self, writer: asyncio.StreamWriter, message: str, details: str) -> None:
        """Send an error response."""
        response = {"status": "error", "message": message, "details": details}
        writer.write(json.dumps(response).encode("utf-8"))
        # We don't await drain here as we are about to close in the finally block anyway
