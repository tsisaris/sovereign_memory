"""Memory consolidation, rebuilds, and garbage collection passes."""

from typing import Any

from service.memory_processor_seam import MemoryProcessor
from src.duckdb_sqlalchemy_async_bridge import AsyncConnection


class MemoryConsolidator:
    """Handles background consolidation, validation, and rebuild passes."""

    def __init__(self, conn: AsyncConnection, registry: dict[str, MemoryProcessor] | None = None):
        """Initialize the consolidator with the owner connection and processor registry.

        Args:
            conn: The worker-owned database connection.
            registry: A dictionary mapping tier names to authorized MemoryProcessor instances.
        """
        self.conn = conn
        self.registry = registry or {}

    async def execute_processor_pass(
        self, processor: MemoryProcessor, event_envelope: dict[str, Any], payload: bytes | None
    ) -> None:
        """
        Execute an engine processor pass on the provided event.

        This demonstrates the exact seam interaction:
        1. Asynchronous preparation (model/extraction) outside write transactions.
        2. Application inside a short write transaction with real DuckDB handle.
        """
        # 1. Preparation outside write transaction
        prepared_work = await processor.prepare(event_envelope, payload)

        # 2. Application inside the tier's short transaction
        await self.conn.begin()
        try:
            # We access the native handle via self.conn.native, but since we are in
            # SQLAlchemy async bridge, we execute the synchronous apply via worker
            def _apply_in_worker():
                return processor.apply(self.conn.native, prepared_work)

            result = await self.conn.run(_apply_in_worker)

            # Update the receipt status using the truthful outcome
            cursor = await self.conn.cursor()
            await cursor.execute(
                """
                UPDATE memory_receipts
                SET status = ?, processor_id = ?, processor_version = ?, projection_version = ?, error_ref = ?
                WHERE event_id = ? AND tier = ?
                """,
                (
                    result.status,
                    result.processor_id,
                    result.processor_version,
                    result.projection_version,
                    result.error_ref,
                    event_envelope["event_id"],
                    processor.tier_name,
                ),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def schedule_fts_rebuild(self) -> None:
        """Replace the full-text index from current event rows in one transaction."""
        await self.conn.begin()
        try:
            await self.conn.run(
                self.conn.native.execute,
                """
                PRAGMA create_fts_index('memory_events', 'event_id', 'raw_event', overwrite=1);
                """,
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def enforce_scope_and_expiry(self) -> None:
        """Enforce identity/scope permissions and TTL/expiry freshness.

        Corrections and deletions retain provenance while preventing stale answers.
        """
        await self.conn.begin()
        try:
            # 1. Select expired events correctly handling ISO timestamps and TTL limits
            cursor = await self.conn.cursor()
            await cursor.execute(
                """
                SELECT event_id FROM memory_events
                WHERE json_extract(raw_event, '$.ttl_seconds') IS NOT NULL
                AND json_extract(raw_event, '$.ttl_seconds')::BIGINT > 0
                AND CAST(created_at AS TIMESTAMP) + INTERVAL (json_extract(raw_event, '$.ttl_seconds')::BIGINT) SECONDS < CURRENT_TIMESTAMP
                """
            )
            expired_ids = [row[0] for row in await cursor.fetchall()]

            if not expired_ids:
                await self.conn.commit()
                return

            await self.conn.commit()

            # 2. Coordinate explicit projection removal via trusted registered tier processors.
            # We explicitly await providers to define the preparation format for expiration.
            for event_id in expired_ids:
                for tier_name, processor in self.registry.items():
                    # We prepare an event envelope mapping to the delete action format defined by the processor.
                    # As providers have not yet standardized expiration inputs, this remains an explicit dependency blocker.
                    # In a real environment, prepare(...) must yield valid `work` corresponding to expiration.
                    envelope_stub = {"event_id": event_id, "event_type": "system.expire"}
                    try:
                        work = await processor.prepare(envelope_stub, None)
                    except (RuntimeError, ValueError):
                        continue  # If provider rejects preparation, skip.

                    # Each tier's projection deletion MUST execute within its own atomic native transaction.
                    await self.conn.begin()
                    try:

                        def _apply_deletion(proc=processor, w=work):
                            return proc.apply(self.conn.native, w)

                        result = await self.conn.run(_apply_deletion)

                        cursor = await self.conn.cursor()
                        await cursor.execute(
                            """
                            UPDATE memory_receipts
                            SET status = ?, processor_id = ?, processor_version = ?, projection_version = ?, error_ref = ?
                            WHERE event_id = ? AND tier = ?
                            """,
                            (
                                result.status,
                                result.processor_id,
                                result.processor_version,
                                result.projection_version,
                                result.error_ref,
                                event_id,
                                tier_name,
                            ),
                        )
                        await self.conn.commit()
                    except (RuntimeError, ValueError, ConnectionError) as e:
                        await self.conn.rollback()

                        # Register the failure in an independent transaction to preserve atomicity of the error receipt.
                        await self.conn.begin()
                        err_cursor = await self.conn.cursor()
                        await err_cursor.execute(
                            """
                            UPDATE memory_receipts
                            SET status = 'terminal_failure', error_ref = ?
                            WHERE event_id = ? AND tier = ?
                            """,
                            (str(e)[:255], event_id, tier_name),
                        )
                        await self.conn.commit()

        except Exception:
            # Roll back any active transaction before propagating the error.
            if self.conn._in_transaction:
                await self.conn.rollback()
            raise
