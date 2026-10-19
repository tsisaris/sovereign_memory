"""Route captured events to registered engines and record truthful receipts.

For each owed (event, tier) pair the dispatcher claims the receipt durably,
asks the tier's registered processor to prepare outside any write transaction,
then runs the processor's apply and the receipt update as ONE job on the
owner's worker, inside one native transaction. The projection and its receipt
therefore commit or roll back together, per tier. A tier with no registered
processor is left pending; the dispatcher never invents a result. An event the
owner has already removed (expired or forgotten) is never projected again: its
owed receipts end as terminal failures naming the removal.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import duckdb
from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_owner_core import TIERS, OwnerCore
from service.memory_processor_seam import MemoryProcessor, OwnerReader, TerminalProcessingError

# Receipt states a processor may report from apply; pending and processing are
# the owner's own bookkeeping states and are never a processor's answer.
# Only the first two mean the projection happened; for any other reported
# state the projection is rolled back and the state is recorded separately.
SUCCESS_STATUSES = frozenset({"processed", "processed_no_new_fact"})
REPORTABLE_STATUSES = SUCCESS_STATUSES | frozenset(
    {"retryable_failure", "terminal_failure", "unknown"}
)
logger = logging.getLogger(__name__)


class RemovedEventError(TerminalProcessingError):
    """The event was expired or forgotten before this tier projected it; it never will be.

    It carries the removal event's id as its message.
    """


def _now() -> str:
    """Return the current UTC instant as ISO 8601 text."""
    return datetime.now(timezone.utc).isoformat()


class ProcessorRegistry:
    """Hold at most one processor per tier.

    It only stores and returns processors; it never calls them.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._processors: dict[str, MemoryProcessor] = {}

    def register(self, processor: MemoryProcessor) -> None:
        """Add a processor for its tier_name; raises ValueError for an unknown or taken tier."""
        tier = processor.tier_name
        if tier not in TIERS:
            raise ValueError(f"Unknown tier '{tier}'")
        if tier in self._processors:
            raise ValueError(f"Tier '{tier}' already has a processor")
        self._processors[tier] = processor

    def get(self, tier: str) -> MemoryProcessor | None:
        """Return the tier's processor, or None when none is registered."""
        return self._processors.get(tier)

    def tiers(self) -> list[str]:
        """Return the tiers that have a processor."""
        return [tier for tier in TIERS if tier in self._processors]


def make_reader(conn: AsyncConnection) -> OwnerReader:
    """Build the reader handed to processors: it runs one function on the owner's worker.

    The function receives the owner's native handle between transactions, so
    it sees committed state only and must not write, commit or close.
    """

    async def read(func):
        """Run func(native handle) as a single worker job and return its result."""
        return await conn.run(func, conn.native)

    return read


class MemoryDispatcher:
    """Process owed receipts through registered engines on the owner connection."""

    def __init__(self, conn: AsyncConnection, registry: ProcessorRegistry) -> None:
        """Bind the owner's connection and registry, and hand each processor a reader."""
        self.conn = conn
        self.core = OwnerCore(conn)
        self.registry = registry
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        reader = make_reader(conn)
        for tier in registry.tiers():
            attach = getattr(registry.get(tier), "attach_reader", None)
            if attach is not None:
                attach(reader)

    async def initialize_storage(self) -> None:
        """Let each processor that has initialize_storage create its tables, once, at start.

        Each such processor gets its own short owner transaction (committed on
        success, rolled back if it raises, and the error propagates), so storage
        creation never hides inside a read-only callback or a receipt transaction.
        """
        for tier in self.registry.tiers():
            initialize = getattr(self.registry.get(tier), "initialize_storage", None)
            if initialize is None:
                continue

            def job(initialize=initialize) -> None:
                """Run the processor's initialization inside one transaction on the worker."""
                handle = self.conn.native
                handle.begin()
                try:
                    initialize(handle)
                    handle.commit()
                except BaseException:
                    handle.rollback()
                    raise

            await self.conn.run(job)

    async def process_pending(self) -> dict[tuple[str, str], str]:
        """Process every owed pair a registered processor covers, in capture order.

        Returns the receipt status each pair ended with. Pairs for tiers
        without a processor are not touched and stay pending.
        """
        outcomes = {}
        for event_id, tier in await self.core.pending_work(self.registry.tiers()):
            outcomes[(event_id, tier)] = await self.process(event_id, tier)
        await self._finish_rebuilds()
        return outcomes

    async def rebuild(self, tier: str, request_id: str, max_items: int) -> dict[str, Any]:
        """Reset a bounded source-backed projection once, then replay through normal receipts.

        This is a trusted Owner operation. The request id makes retries resume without
        clearing completed work. Old receipts are archived before being made pending;
        originals and removals never change. A new id explicitly supersedes an older
        rebuild. Model work remains in the normal prepare/apply path.
        """
        processor = self.registry.get(tier)
        reset = getattr(processor, "reset_projection", None)
        identity = getattr(processor, "embedding_identity", None)
        if not callable(reset) or not isinstance(identity, str):
            raise ValueError(f"Tier {tier!r} has no supported embedding projection rebuild")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("A nonempty rebuild request id is required")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
            raise ValueError("Rebuild max_items must be a positive integer")

        def begin() -> str:
            """Validate recoverability and reset projections/receipts in one owner transaction."""
            handle = self.conn.native
            handle.begin()
            ended = False
            try:
                transaction_id = handle.execute("SELECT current_transaction_id()").fetchone()[0]
                previous = handle.execute(
                    "SELECT tier, embedding_identity, status FROM memory_rebuilds WHERE request_id = ?",
                    [request_id],
                ).fetchone()
                if previous is not None:
                    if previous[:2] != (tier, identity):
                        raise ValueError(
                            "Rebuild request id was already used with another identity"
                        )
                    handle.commit()
                    return previous[2]
                sources = handle.execute(
                    "SELECT e.raw_event, e.schema_version, p.payload, p.payload_sha256 "
                    "FROM memory_events e JOIN memory_receipts r USING (event_id) "
                    "LEFT JOIN memory_payloads p USING (event_id) WHERE r.tier = ? "
                    "AND (e.schema_version LIKE '2.%' OR json_extract_string(e.raw_event, '$.tier') = r.tier) "
                    "ORDER BY e.rowid LIMIT ?",
                    [tier, max_items + 1],
                ).fetchall()
                if len(sources) > max_items:
                    raise ValueError("Source events exceed the requested rebuild item limit")
                event_ids = [OwnerCore.verify_stored_event(*row)[0]["event_id"] for row in sources]
                handle.execute(
                    "INSERT INTO memory_rebuilds VALUES (?, ?, ?, 'processing', ?)",
                    [request_id, tier, identity, _now()],
                )
                if event_ids:
                    cursor = handle.execute(
                        "SELECT * FROM memory_receipts WHERE tier = ? AND event_id IN (SELECT unnest(?))",
                        [tier, event_ids],
                    )
                    columns = [column[0] for column in cursor.description]
                    receipts = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
                    handle.executemany(
                        "INSERT INTO memory_rebuild_receipts VALUES (?, ?, ?, ?)",
                        [
                            (request_id, receipt["event_id"], tier, json.dumps(receipt))
                            for receipt in receipts
                        ],
                    )
                reset(handle, max_items)
                if (
                    handle.execute("SELECT current_transaction_id()").fetchone()[0]
                    != transaction_id
                ):
                    ended = True
                    raise RuntimeError(
                        "Processor ended the Owner rebuild transaction; committed changes cannot be undone"
                    )
                if event_ids:
                    handle.execute(
                        "UPDATE memory_receipts SET status = CASE WHEN event_id IN "
                        "(SELECT target_event_id FROM memory_removals) THEN 'terminal_failure' ELSE 'pending' END, "
                        "attempt_id = NULL, attempted_at = NULL, completed_at = NULL, error_ref = CASE "
                        "WHEN event_id IN (SELECT target_event_id FROM memory_removals) "
                        "THEN 'Source was removed; excluded from rebuild' ELSE NULL END "
                        "WHERE tier = ? AND event_id IN (SELECT unnest(?))",
                        [tier, event_ids],
                    )
                handle.execute(
                    "UPDATE memory_rebuilds SET status = 'superseded' "
                    "WHERE tier = ? AND request_id != ? AND status IN ('processing', 'failed')",
                    [tier, request_id],
                )
                handle.commit()
                return "processing"
            except BaseException:
                if not ended:
                    handle.rollback()
                raise

        state = await self.conn.run(begin)
        if state == "processing":
            for event_id, selected in (await self.core.pending_work([tier]))[:max_items]:
                await self.process(event_id, selected)
            await self._finish_rebuilds()
        rows = await self.conn.run(
            lambda: self.conn.native.execute(
                "SELECT status FROM memory_rebuilds WHERE request_id = ?", [request_id]
            ).fetchone()
        )
        return {
            "request_id": request_id,
            "tier": tier,
            "embedding_identity": identity,
            "status": rows[0],
        }

    async def _finish_rebuilds(self) -> None:
        """Finish durable rebuild requests only after their current live receipts finish."""

        def job() -> None:
            """Update status on the owner worker without touching any source or projection."""
            handle = self.conn.native
            handle.begin()
            try:
                active = handle.execute(
                    "SELECT request_id, tier, embedding_identity FROM memory_rebuilds WHERE status = 'processing'"
                ).fetchall()
                for request_id, tier, identity in active:
                    processor = self.registry.get(tier)
                    if processor is None:
                        continue
                    if getattr(processor, "embedding_identity", None) != identity:
                        handle.execute(
                            "UPDATE memory_rebuilds SET status = 'failed' WHERE request_id = ?",
                            [request_id],
                        )
                        continue
                    states = [
                        row[0]
                        for row in handle.execute(
                            "SELECT r.status FROM memory_receipts r JOIN memory_events e USING (event_id) "
                            "LEFT JOIN memory_removals m ON m.target_event_id = e.event_id "
                            "WHERE r.tier = ? AND m.target_event_id IS NULL AND "
                            "(e.schema_version LIKE '2.%' OR json_extract_string(e.raw_event, '$.tier') = r.tier)",
                            [tier],
                        ).fetchall()
                    ]
                    if any(
                        state in ("pending", "processing", "retryable_failure") for state in states
                    ):
                        continue
                    status = (
                        "completed"
                        if all(state in SUCCESS_STATUSES for state in states)
                        else "failed"
                    )
                    handle.execute(
                        "UPDATE memory_rebuilds SET status = ? WHERE request_id = ?",
                        [status, request_id],
                    )
                handle.commit()
            except BaseException:
                handle.rollback()
                raise

        await self.conn.run(job)

    async def process_event(self, event_id: str) -> dict[str, str]:
        """Process one event for every registered tier and return tier -> receipt status."""
        owed = {
            tier
            for ev, tier in await self.core.pending_work(self.registry.tiers())
            if ev == event_id
        }
        return {tier: await self.process(event_id, tier) for tier in TIERS if tier in owed}

    async def process(self, event_id: str, tier: str) -> str:
        """Run one attempt for an event and tier and return the receipt status it ended with.

        Preparation failure or apply failure is recorded as a retryable (or,
        for TerminalProcessingError, terminal) failure receipt; the projection
        of a failed apply is rolled back. Never marks success without the
        processor reporting it.
        """
        processor = self.registry.get(tier)
        if processor is None:
            raise ValueError(f"No processor registered for tier '{tier}'")
        lock = self._locks.setdefault((event_id, tier), asyncio.Lock())
        async with lock:
            attempt_id = await self._claim(event_id, tier)
            if attempt_id is None:
                return await self._status(event_id, tier)
            try:
                removal = await self.core.removal_of(event_id)
                if removal is not None:
                    raise RemovedEventError(removal)
                event, payload = await self.core.load_event(event_id)
                prepared = await processor.prepare(event, payload)
            except TerminalProcessingError as error:
                return await self._record_failure(
                    event_id, tier, attempt_id, "terminal_failure", error
                )
            except Exception as error:
                logger.exception("Preparing %s for tier %s failed", event_id, tier)
                return await self._record_failure(
                    event_id, tier, attempt_id, "retryable_failure", error
                )
            try:
                return await self.conn.run(
                    self._apply_and_record, processor, prepared, event_id, tier, attempt_id
                )
            except TerminalProcessingError as error:
                return await self._record_failure(
                    event_id, tier, attempt_id, "terminal_failure", error
                )
            except Exception as error:
                logger.exception("Applying %s for tier %s failed", event_id, tier)
                return await self._record_failure(
                    event_id, tier, attempt_id, "retryable_failure", error
                )

    async def _status(self, event_id: str, tier: str) -> str:
        """Return the receipt's current status."""
        cursor = await self.conn.cursor()
        await cursor.execute(
            "SELECT status FROM memory_receipts WHERE event_id = ? AND tier = ?", (event_id, tier)
        )
        return (await cursor.fetchone())[0]

    async def _claim(self, event_id: str, tier: str) -> str | None:
        """Durably mark an owed receipt as processing and return its attempt id.

        Returns None when the receipt is already finished (processed, no new
        fact, or terminal), so a repeated request does no work.
        """

        def job() -> str | None:
            """Claim in one short transaction on the worker."""
            handle = self.conn.native
            handle.begin()
            try:
                row = handle.execute(
                    "SELECT status FROM memory_receipts WHERE event_id = ? AND tier = ?",
                    (event_id, tier),
                ).fetchone()
                if row is None or row[0] not in ("pending", "retryable_failure", "processing"):
                    handle.rollback()
                    return None
                attempt_id = str(uuid.uuid4())
                handle.execute(
                    "UPDATE memory_receipts SET status = 'processing', attempt_id = ?, "
                    "attempted_at = ?, completed_at = NULL, error_ref = NULL "
                    "WHERE event_id = ? AND tier = ?",
                    (attempt_id, _now(), event_id, tier),
                )
                handle.commit()
                return attempt_id
            except BaseException:
                handle.rollback()
                raise

        return await self.conn.run(job)

    def _apply_and_record(
        self,
        processor: MemoryProcessor,
        prepared: Any,
        event_id: str,
        tier: str,
        attempt_id: str,
    ) -> str:
        """Worker job: apply the projection and write its receipt in one transaction.

        Only a success status commits the projection with its receipt. When apply
        raises, reports a failure or unknown status, reports an unreportable
        status, ends the transaction itself, or the receipt's attempt is no
        longer current, the projection is rolled back; a reported failure is then
        recorded in a fresh transaction. An event removed while it was prepared
        is refused before apply runs.
        """
        handle: duckdb.DuckDBPyConnection = self.conn.native
        handle.begin()
        ended = False
        try:
            started = handle.execute("SELECT current_transaction_id()").fetchone()[0]
            removed = handle.execute(
                "SELECT removal_event_id FROM memory_removals WHERE target_event_id = ?",
                (event_id,),
            ).fetchone()
            if removed is not None:
                # Removed while this tier prepared it: never write the projection.
                raise RemovedEventError(removed[0])
            result = processor.apply(handle, prepared)
            if handle.execute("SELECT current_transaction_id()").fetchone()[0] != started:
                ended = True
                raise TerminalProcessingError(
                    "Processor ended the owner's transaction: apply must not commit, and "
                    "whatever it already committed cannot be undone"
                )
            status = getattr(result, "status", None)
            if status not in REPORTABLE_STATUSES:
                raise RuntimeError(f"Processor reported unreportable status {status!r}")
            error_ref = getattr(result, "error_ref", None)
            if status not in SUCCESS_STATUSES:
                # The processor reported no completed projection: discard its writes first.
                ended = True
                handle.rollback()
                handle.begin()
                ended = False
                error_ref = error_ref or (
                    f"processor reported {status}; its projection was rolled back"
                )
            handle.execute(
                "UPDATE memory_receipts SET status = ?, processor_id = ?, processor_version = ?, "
                "projection_version = ?, completed_at = ?, error_ref = ? "
                "WHERE event_id = ? AND tier = ? AND attempt_id = ? AND status = 'processing'",
                (
                    status,
                    result.processor_id,
                    result.processor_version,
                    result.projection_version,
                    _now(),
                    error_ref,
                    event_id,
                    tier,
                    attempt_id,
                ),
            )
            current = handle.execute(
                "SELECT status, attempt_id FROM memory_receipts WHERE event_id = ? AND tier = ?",
                (event_id, tier),
            ).fetchone()
            if current != (status, attempt_id):
                raise RuntimeError("Receipt attempt is no longer current; result discarded")
            handle.commit()
            return status
        except BaseException:
            # When apply itself ended the transaction there is nothing left to roll back.
            if not ended:
                handle.rollback()
            raise

    async def _record_failure(
        self, event_id: str, tier: str, attempt_id: str, status: str, error: BaseException
    ) -> str:
        """Record a failed attempt on the receipt in its own transaction and return the status."""
        reference = f"{type(error).__name__}: {error}"

        def job() -> str:
            """Write the failure receipt on the worker."""
            handle = self.conn.native
            handle.begin()
            try:
                handle.execute(
                    "UPDATE memory_receipts SET status = ?, completed_at = ?, error_ref = ? "
                    "WHERE event_id = ? AND tier = ? AND attempt_id = ? AND status = 'processing'",
                    (status, _now(), reference, event_id, tier, attempt_id),
                )
                handle.commit()
                return status
            except BaseException:
                handle.rollback()
                raise

        return await self.conn.run(job)
