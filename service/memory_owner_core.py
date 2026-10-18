"""Core logic for the Sovereign Owner Service managing DuckDB transactions and event ingestion."""

import json
from datetime import datetime, timezone

from service.memory_owner_schema import MemoryEventValidator
from src.duckdb_sqlalchemy_async_bridge import AsyncConnection


class OwnerCore:
    """Manages the lifecycle of memory events in DuckDB with per-tier transactional atomicity."""

    def __init__(self, conn: AsyncConnection):
        """Initialize the core with a specific worker-owned database connection."""
        self.conn = conn
        self.validator = MemoryEventValidator()

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
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def ingest_event(self, event_data: dict, payload_content: bytes | None = None) -> dict:
        """
        Ingest a memory event, validate it, and write it and its initial receipts durably.
        Returns the event dictionary on success.
        Raises ValueError if validation fails.
        """
        # Validate syntax and semantics
        self.validator.validate_event(event_data, payload_content)
        import hashlib

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

        # Initial receipts to be inserted must always be 'pending' initially
        # The user's requested projection details might be recorded, but not their status.
        receipts = []
        for tier in ["episodic", "temporal", "relational", "personalization"]:
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

                    # Compare full immutable canonical envelope optionally (simplest safe bound is restoring the original identical envelope)
                    event_data = json.loads(existing_raw_json)

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
