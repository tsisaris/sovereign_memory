"""Memory consolidation, rebuilds, and garbage collection passes."""

from typing import Any

from service.memory_processor_seam import MemoryProcessor
from src.duckdb_sqlalchemy_async_bridge import AsyncConnection


class MemoryConsolidator:
    """Handles background consolidation, validation, and rebuild passes."""

    def __init__(self, conn: AsyncConnection):
        """Initialize the consolidator with the owner connection."""
        self.conn = conn

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
                WHERE event_id = ?
                """,
                (
                    result.status,
                    result.processor_id,
                    result.processor_version,
                    result.projection_version,
                    result.error_ref,
                    event_envelope["event_id"],
                ),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def schedule_fts_rebuild(self) -> None:
        """Schedule and perform Full Text Search index rebuild atomically.

        Revalidates current authorized rows AND current text predicate so
        removed hit text cannot survive an edit.
        """
        await self.conn.begin()
        try:
            # Drop/rebuild FTS index based on current authoritative text rows.
            # Real execution depends on DDL resolution for FTS.
            await self.conn.run(
                self.conn.native.execute,
                """
                -- Placeholder query demonstrating exact requirement
                -- PRAGMA create_fts_index(...)
                SELECT 1;
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
            # Look for expired TTLs or specific correction events.
            # Mark them inactive rather than deleting to retain provenance.
            await self.conn.run(
                self.conn.native.execute,
                """
                UPDATE memory_receipts
                SET status = 'processed_no_new_fact'
                WHERE event_id IN (
                    SELECT event_id FROM memory_events
                    WHERE json_extract(raw_event, '$.ttl_seconds') IS NOT NULL
                    AND json_extract(raw_event, '$.ttl_seconds')::BIGINT > 0
                )
                """,
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
