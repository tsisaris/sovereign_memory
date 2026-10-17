"""Schema and semantic validation for memory events."""

import hashlib
import json
from pathlib import Path

from jsonschema import Draft7Validator

# Deriving path from file location as per rules
SCHEMA_PATH = Path(__file__).parent / "schemas" / "memory_event_schema.json"


class MemoryEventValidator:
    """Validate memory events syntactically and semantically."""

    def __init__(self):
        """Load and prepare the JSON schema validator."""
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.schema = json.load(f)
        Draft7Validator.check_schema(self.schema)
        self.validator = Draft7Validator(self.schema)

    def validate_event(self, event_data: dict, payload_content: bytes | None = None) -> None:
        """
        Validate an event dictionary syntactically against the schema and semantically.

        Args:
            event_data: The parsed JSON event.
            payload_content: Optional raw payload bytes for SHA256 validation if payload_ref implies inline or provided content.
        """
        errors = list(self.validator.iter_errors(event_data))
        if errors:
            raise ValueError(f"Event failed schema validation: {errors[0].message}")

        # Semantic validation: v2 envelopes
        if "schema_version" in event_data and event_data["schema_version"].startswith("2."):
            self._validate_semantic_v2(event_data, payload_content)

    def _validate_semantic_v2(self, event_data: dict, payload_content: bytes | None) -> None:
        """Enforce semantic validation rules for v2 envelopes."""
        # Validate that tier_receipts IDs match the envelope event_id
        event_id = event_data["event_id"]
        tier_receipts = event_data.get("tier_receipts", {})
        for tier, receipt in tier_receipts.items():
            if receipt["event_id"] != event_id:
                raise ValueError(
                    f"Receipt event_id {receipt['event_id']} for tier {tier} does not match envelope event_id {event_id}"
                )

        # Idempotency and status checking
        # As per docs: "ingestion must not invent four successful receipts" (legacy)
        # For v2, new events generally should have pending receipts, but we rely on core for insertion logic.

        # Payload validation if content is provided
        if payload_content is not None:
            expected_hash = event_data["payload_sha256"]
            actual_hash = hashlib.sha256(payload_content).hexdigest()
            if expected_hash != actual_hash:
                raise ValueError(
                    f"Payload SHA256 mismatch. Expected {expected_hash}, got {actual_hash}"
                )
