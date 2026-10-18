"""Memory consolidation, rebuilds, and garbage collection passes."""

from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_dispatcher import MemoryDispatcher
from service.memory_owner_core import OwnerCore


class MemoryConsolidator:
    """Handles background consolidation, validation, and rebuild passes."""

    def __init__(self, conn: AsyncConnection):
        """Bind the owner's worker-owned database connection.

        Processing itself belongs to MemoryDispatcher, which is passed in where needed.
        """
        self.conn = conn

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

    async def enforce_scope_and_expiry(self, dispatcher: MemoryDispatcher) -> dict[str, dict[str, str]]:
        """Expire events whose ttl has elapsed through the real processor interface.

        For each expired event the owner captures its own "system.expire" event
        (a complete v2 envelope with stored, hash-verified payload bytes) and
        the dispatcher sends it to every registered tier's processor, which
        removes its projection atomically with its receipt. The expired event
        and the expiry event stay stored for provenance. Returns expiry event id
        -> tier -> receipt status; tiers without a processor stay pending.
        """
        results: dict[str, dict[str, str]] = {}
        core = OwnerCore(self.conn)
        for target_id, expires_at, ttl in await core.expired_events():
            expiry_id = await core.ingest_system_expiry(target_id, expires_at, ttl)
            results[expiry_id] = await dispatcher.process_event(expiry_id)
        return results
