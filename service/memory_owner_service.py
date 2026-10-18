"""Owner Service for sovereign memory operating over a Unix socket."""

import asyncio
import json
import logging
from pathlib import Path

from service.memory_owner_core import OwnerCore
from src.duckdb_sqlalchemy_async_bridge import connect as duckdb_connect

logger = logging.getLogger(__name__)


class OwnerService:
    """The unified owner service running the DuckDB connection and socket server."""

    def __init__(self, socket_path: str | Path, db_path: str | Path):
        """Initialize the service with socket and db paths."""
        self.socket_path = Path(socket_path)
        self.db_path = Path(db_path)
        self.conn = None
        self.core = None
        self.server = None

    async def start(self) -> None:
        """Initialize database connection and start UNIX socket server."""
        # Initialize schema once at startup using a throwaway connection
        conn = await duckdb_connect(str(self.db_path))
        core = OwnerCore(conn)
        await core.initialize_schema()
        await conn.close()

        # Ensure socket directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        self.server = await asyncio.start_unix_server(self.handle_client, str(self.socket_path))
        logger.info(f"Owner service listening on {self.socket_path}")

    async def stop(self) -> None:
        """Stop the server and close the database connection."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

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

            event_data = payload.get("event")
            payload_content_hex = payload.get("payload_content_hex")
            payload_content = bytes.fromhex(payload_content_hex) if payload_content_hex else None

            if not event_data:
                self._send_error(writer, "Missing 'event' in payload", "")
                return

            try:
                # Instantiate per-request independent connection per the concurrency rule
                conn = await duckdb_connect(str(self.db_path))
                core = OwnerCore(conn)
                result_event = await core.ingest_event(event_data, payload_content)

                response = {"status": "success", "event": result_event}
                writer.write(json.dumps(response).encode("utf-8"))
                await writer.drain()
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
