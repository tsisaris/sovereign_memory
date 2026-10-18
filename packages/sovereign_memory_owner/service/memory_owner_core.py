"""Core logic for the Sovereign Owner Service managing DuckDB transactions and event ingestion."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_owner_schema import MemoryEventValidator


TIERS = ("episodic", "temporal", "relational", "personalization")
SYSTEM_EVENT_PREFIX = "system."
SYSTEM_ACTOR = "owner-system"


def parse_instant(text: str) -> datetime:
    """Parse an ISO 8601 timestamp into an aware UTC datetime.

    A timestamp without a zone is read as UTC. It raises ValueError for text
    that is not ISO 8601; it never guesses.
    """
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class OwnerCore:
    """Manages the lifecycle of memory events in DuckDB with per-tier transactional atomicity."""

    def __init__(self, conn: AsyncConnection):
        """Initialize the core with a specific worker-owned database connection."""
        self.conn = conn
        self.validator = MemoryEventValidator()

    @staticmethod
    def _immutable_event(event_data: dict) -> dict:
        """Retain envelope and processor identity while excluding mutable receipt state."""
        immutable = event_data.copy()
        if event_data["schema_version"].startswith("2."):
            immutable["tier_receipts"] = {
                tier: {
                    key: receipt[key]
                    for key in (
                        "event_id",
                        "processor_id",
                        "processor_version",
                        "projection_version",
                    )
                }
                for tier, receipt in event_data["tier_receipts"].items()
            }
        return immutable

    async def initialize_schema(self) -> None:
        """Create durable tables for events and receipts if they do not exist."""
        # Note: In a real Alembic setup, this would be a migration.
        # For the standalone core, we ensure tables exist.
        await self.conn.begin()
        try:
            await self.conn.run(
                self.conn.native.execute,
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id VARCHAR PRIMARY KEY,
                    schema_version VARCHAR NOT NULL,
                    actor_agent_id VARCHAR,
                    event_type VARCHAR,
                    created_at VARCHAR NOT NULL,
                    payload_ref VARCHAR,
                    payload_sha256 VARCHAR,
                    exactness VARCHAR,
                    outcome VARCHAR,
                    raw_event JSON NOT NULL
                );
                """,
            )
            await self.conn.run(
                self.conn.native.execute,
                """
                CREATE TABLE IF NOT EXISTS memory_receipts (
                    event_id VARCHAR,
                    tier VARCHAR,
                    processor_id VARCHAR NOT NULL,
                    processor_version VARCHAR NOT NULL,
                    projection_version VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    attempt_id VARCHAR,
                    attempted_at VARCHAR,
                    completed_at VARCHAR,
                    error_ref VARCHAR,
                    PRIMARY KEY (event_id, tier),
                    FOREIGN KEY (event_id) REFERENCES memory_events(event_id)
                );
                """,
            )
            await self.conn.run(
                self.conn.native.execute,
                """
                CREATE TABLE IF NOT EXISTS memory_payloads (
                    event_id VARCHAR PRIMARY KEY,
                    payload_sha256 VARCHAR NOT NULL,
                    payload BLOB NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES memory_events(event_id)
                );
                """,
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    @staticmethod
    async def _store_payload_if_missing(cursor, event_id: str, payload: bytes) -> None:
        """Write the payload row of an already captured event when it has none.

        The caller has already verified the bytes against the stored hash. It
        never replaces bytes that are already stored.
        """
        await cursor.execute("SELECT 1 FROM memory_payloads WHERE event_id = ?", (event_id,))
        if await cursor.fetchone() is None:
            await cursor.execute(
                "INSERT INTO memory_payloads (event_id, payload_sha256, payload) VALUES (?, ?, ?)",
                (event_id, hashlib.sha256(payload).hexdigest(), payload),
            )

    async def load_event(self, event_id: str) -> tuple[dict, bytes | None]:
        """Return the stored envelope and its stored payload bytes, verified.

        The bytes are checked against the envelope's hash (version 2) or the
        hash recorded at capture. Raises ValueError for an unknown event, for
        a version 2 event whose bytes were never stored, or on a hash mismatch.
        It never repairs or rewrites anything.
        """
        cursor = await self.conn.cursor()
        await cursor.execute(
            """
            SELECT e.raw_event, e.schema_version, p.payload, p.payload_sha256
            FROM memory_events e LEFT JOIN memory_payloads p USING (event_id)
            WHERE e.event_id = ?
            """,
            (event_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Unknown event {event_id}")
        raw_event, schema_version, payload, stored_hash = row
        event = json.loads(raw_event)
        if payload is None:
            if schema_version.startswith("2."):
                raise ValueError(
                    f"Event {event_id} has no stored payload bytes; retry the capture to supply them"
                )
            return event, None
        payload = bytes(payload)
        if hashlib.sha256(payload).hexdigest() != stored_hash or (
            schema_version.startswith("2.") and stored_hash != event["payload_sha256"]
        ):
            raise ValueError(f"Stored payload for event {event_id} fails its hash check")
        return event, payload

    async def pending_work(self, tiers: list[str]) -> list[tuple[str, str]]:
        """List (event_id, tier) pairs still owed, in capture order.

        Owed means the receipt is pending, retryable_failure or processing (an
        attempt a crash left unfinished), for the given tiers. A legacy event
        is owed only to its own tier. It reads only.
        """
        if not tiers:
            return []
        cursor = await self.conn.cursor()
        marks = ", ".join("?" for _ in tiers)
        await cursor.execute(
            f"""
            SELECT r.event_id, r.tier
            FROM memory_receipts r JOIN memory_events e USING (event_id)
            WHERE r.status IN ('pending', 'retryable_failure', 'processing')
              AND r.tier IN ({marks})
              AND (e.schema_version LIKE '2.%' OR json_extract_string(e.raw_event, '$.tier') = r.tier)
            ORDER BY e.rowid, r.tier
            """,
            tuple(tiers),
        )
        return [(row[0], row[1]) for row in await cursor.fetchall()]

    async def expired_events(self) -> list[tuple[str, str, int]]:
        """List legacy events whose time to live has run out as (event_id, expires_at, ttl_seconds).

        expires_at is the ISO instant the event's own timestamp plus its
        ttl_seconds reaches; only events with a positive ttl are listed. Only
        legacy envelopes carry a ttl; version 2 has no expiry field. Reads only.
        """
        cursor = await self.conn.cursor()
        await cursor.execute(
            """
            SELECT event_id, created_at, json_extract(raw_event, '$.ttl_seconds')::BIGINT
            FROM memory_events
            WHERE schema_version = '1.0.0'
              AND json_extract(raw_event, '$.ttl_seconds') IS NOT NULL
              AND json_extract(raw_event, '$.ttl_seconds')::BIGINT > 0
            ORDER BY rowid
            """
        )
        now = datetime.now(timezone.utc)
        expired = []
        for event_id, created_at, ttl in await cursor.fetchall():
            expires_at = parse_instant(created_at) + timedelta(seconds=ttl)
            if expires_at <= now:
                expired.append((event_id, expires_at.isoformat(), ttl))
        return expired

    async def ingest_system_expiry(self, target_event_id: str, expires_at: str, ttl: int) -> str:
        """Capture the owner's own "system.expire" event for an expired event and return its id.

        The envelope is a complete, schema-valid version 2 event with real
        stored payload bytes and hash, authored by the owner principal, so the
        tiers remove their projections through the ordinary processor
        interface. It is deterministic: asking again returns the same event
        without changing it. Its receipts start pending.
        """
        payload = json.dumps(
            {
                "operation": "expire",
                "target_event_id": target_event_id,
                "ttl_seconds": ttl,
                "reason": "ttl_seconds elapsed",
            },
            sort_keys=True,
        ).encode("utf-8")
        target, _ = await self.load_event(target_event_id)
        event_id = f"{SYSTEM_EVENT_PREFIX}expire:{target_event_id}"
        envelope = {
            "schema_version": "2.0.0",
            "event_id": event_id,
            "actor_agent_id": SYSTEM_ACTOR,
            "event_type": f"{SYSTEM_EVENT_PREFIX}expire",
            "created_at": expires_at,
            "source_artifact_id": target.get("source_artifact_id"),
            "work_id": None,
            "parent_event_id": None,
            "causation_event_id": target_event_id,
            "source_revision": f"owner-expiry-of-{target_event_id}",
            "recipe_version": None,
            "model_occupant_id": None,
            "program_version": None,
            "payload_ref": "inline",
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "outcome": "expired",
            "exactness": "derived",
            "tier_receipts": {
                tier: {
                    "event_id": event_id,
                    "processor_id": "unknown",
                    "processor_version": "0.0.0",
                    "projection_version": "0.0.0",
                    "status": "pending",
                    "attempt_id": None,
                    "attempted_at": None,
                    "completed_at": None,
                    "error_ref": None,
                }
                for tier in TIERS
            },
        }
        await self.ingest_event(envelope, payload, trusted_system=True)
        return event_id

    async def ingest_event(
        self,
        event_data: dict,
        payload_content: bytes | None = None,
        *,
        trusted_system: bool = False,
    ) -> dict:
        """
        Ingest a memory event, validate it, and write it, its payload bytes and its initial receipts durably.

        A version 2 event's verified payload bytes are stored in the same
        transaction as the event, so a restart or retry can prepare the work
        again from durable bytes. A retry of an already captured event whose
        bytes were never stored backfills them once they verify against the
        stored hash. Event types starting with "system." are accepted only from
        the owner's own code (trusted_system=True), never from a client.
        Returns the event dictionary on success.
        Raises ValueError if validation fails.
        """
        # Durable receipt output must not alter a caller's retryable input envelope.
        event_data = event_data.copy()
        # Validate syntax and semantics
        self.validator.validate_event(event_data, payload_content)
        if (
            str(event_data.get("event_type", event_data.get("content_type", ""))).startswith(
                SYSTEM_EVENT_PREFIX
            )
            and not trusted_system
        ):
            raise ValueError(
                f"Event type prefix '{SYSTEM_EVENT_PREFIX}' is reserved for the owner's own operations"
            )

        # Resolve/verify all supported refs BEFORE acceptance
        schema_version = event_data.get("schema_version", "1.0.0")
        is_v2 = schema_version.startswith("2.")

        if is_v2:
            payload_ref = event_data.get("payload_ref", "")
            if payload_ref != "inline":
                raise ValueError(
                    f"Unsupported payload_ref '{payload_ref}'. Only 'inline' is supported in this implementation."
                )

            if payload_content is None:
                raise ValueError("Missing inline payload content bytes to verify against SHA256")

            actual_hash = hashlib.sha256(payload_content).hexdigest()
            if event_data.get("payload_sha256", "") != actual_hash:
                raise ValueError(
                    f"Payload hash mismatch: expected {event_data.get('payload_sha256')}, got {actual_hash}"
                )

        event_id = event_data["event_id"]
        schema_version = event_data.get("schema_version", "1.0.0")

        # Normalize to v2 fields for storage where possible
        actor_agent_id = event_data.get("actor_agent_id", event_data.get("agent_id"))
        event_type = event_data.get("event_type", event_data.get("content_type", "unknown"))
        created_at = event_data.get(
            "created_at", event_data.get("timestamp", datetime.now(timezone.utc).isoformat())
        )
        payload_ref = event_data.get("payload_ref", "inline")
        payload_sha256 = event_data.get("payload_sha256", "0" * 64)
        exactness = event_data.get("exactness", "observed")
        outcome = event_data.get("outcome", "unknown")

        raw_event_json = json.dumps(event_data)
        stored_payload = (
            payload_content if is_v2 else str(event_data.get("content", "")).encode("utf-8")
        )

        # Initial receipts to be inserted must always be 'pending' initially
        # The user's requested projection details might be recorded, but not their status.
        receipts = []
        for tier in TIERS:
            if schema_version.startswith("2.") and tier in event_data.get("tier_receipts", {}):
                r = event_data["tier_receipts"][tier]
                receipts.append(
                    (
                        event_id,
                        tier,
                        r.get("processor_id", "unknown"),
                        r.get("processor_version", "0.0.0"),
                        r.get("projection_version", "0.0.0"),
                        "pending",  # Force pending
                        None,
                        None,
                        None,
                        None,
                    )
                )
            else:
                receipts.append(
                    (event_id, tier, "unknown", "0.0.0", "0.0.0", "pending", None, None, None, None)
                )

        await self.conn.begin()
        try:
            # Check for idempotency: if event exists, simply return it (or its current state)
            cursor = await self.conn.cursor()
            await cursor.execute("SELECT 1 FROM memory_events WHERE event_id = ?", (event_id,))
            existing = await cursor.fetchone()
            if existing:
                # Enforce exact identity binding conflict on retry identical
                await cursor.execute(
                    "SELECT actor_agent_id, payload_sha256, raw_event FROM memory_events WHERE event_id = ?",
                    (event_id,),
                )
                existing_meta = await cursor.fetchone()
                if existing_meta:
                    existing_actor, existing_hash, existing_raw_json = existing_meta

                    if existing_actor != actor_agent_id:
                        await self.conn.rollback()
                        raise ValueError(
                            f"Conflict: Event {event_id} already exists with different principal/actor {existing_actor}"
                        )

                    if existing_hash != payload_sha256:
                        await self.conn.rollback()
                        raise ValueError(
                            f"Conflict: Event {event_id} already exists with different payload hash {existing_hash}"
                        )

                    existing_event = json.loads(existing_raw_json)
                    if self._immutable_event(existing_event) != self._immutable_event(event_data):
                        raise ValueError(
                            f"Conflict: Event {event_id} already exists with a different immutable envelope"
                        )
                    event_data = existing_event
                    await self._store_payload_if_missing(cursor, event_id, stored_payload)

                # Return durable current receipts, not incoming state
                await cursor.execute(
                    "SELECT tier, processor_id, processor_version, projection_version, status, attempt_id, attempted_at, completed_at, error_ref FROM memory_receipts WHERE event_id = ?",
                    (event_id,),
                )
                existing_receipts = await cursor.fetchall()

                result_receipts = {}
                for r in existing_receipts:
                    result_receipts[r[0]] = {
                        "event_id": event_id,
                        "processor_id": r[1],
                        "processor_version": r[2],
                        "projection_version": r[3],
                        "status": r[4],
                        "attempt_id": r[5],
                        "attempted_at": r[6],
                        "completed_at": r[7],
                        "error_ref": r[8],
                    }

                event_data["tier_receipts"] = result_receipts
                await self.conn.commit()
                return event_data

            await cursor.execute(
                """
                INSERT INTO memory_events
                (event_id, schema_version, actor_agent_id, event_type, created_at, payload_ref, payload_sha256, exactness, outcome, raw_event)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    schema_version,
                    actor_agent_id,
                    event_type,
                    created_at,
                    payload_ref,
                    payload_sha256,
                    exactness,
                    outcome,
                    raw_event_json,
                ),
            )

            await cursor.execute(
                "INSERT INTO memory_payloads (event_id, payload_sha256, payload) VALUES (?, ?, ?)",
                (event_id, hashlib.sha256(stored_payload).hexdigest(), stored_payload),
            )

            for r in receipts:
                await cursor.execute(
                    """
                    INSERT INTO memory_receipts
                    (event_id, tier, processor_id, processor_version, projection_version, status, attempt_id, attempted_at, completed_at, error_ref)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    r,
                )

            # Reconstruct the return dictionary from the actual durably written pending receipts
            result_receipts = {}
            for r in receipts:
                result_receipts[r[1]] = {
                    "event_id": event_id,
                    "processor_id": r[2],
                    "processor_version": r[3],
                    "projection_version": r[4],
                    "status": r[5],
                    "attempt_id": r[6],
                    "attempted_at": r[7],
                    "completed_at": r[8],
                    "error_ref": r[9],
                }
            event_data["tier_receipts"] = result_receipts

            await self.conn.commit()
            return event_data
        except Exception:
            await self.conn.rollback()
            raise
