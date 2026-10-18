"""Minimal service-side processor contract for sovereign memory engines.

This defines the interface that each downstream engine (Letta, Zep/Graphiti, Cognee, Mem0)
must implement to participate in the unified Owner Service transaction lifecycle.
"""

from typing import Any, Dict, Optional, Protocol

import duckdb


class ProcessorResult:
    """The result of applying a processor's work."""

    def __init__(
        self,
        status: str,
        processor_id: str,
        processor_version: str,
        projection_version: str,
        error_ref: Optional[str] = None,
    ):
        """Initialize the processor result."""
        self.status = status
        self.processor_id = processor_id
        self.processor_version = processor_version
        self.projection_version = projection_version
        self.error_ref = error_ref


class MemoryProcessor(Protocol):
    """The required contract for memory engines to process events safely."""

    @property
    def tier_name(self) -> str:
        """Return the tier identity (e.g., 'episodic', 'temporal', 'relational', 'personalization')."""
        ...

    async def prepare(
        self, event_envelope: Dict[str, Any], verified_payload_bytes: Optional[bytes]
    ) -> Any:
        """
        Perform real engine/model/extraction work OUTSIDE a write transaction.

        Args:
            event_envelope: The v2 memory event dictionary.
            verified_payload_bytes: The raw payload bytes, already verified against SHA256.

        Returns:
            An engine-owned prepared object representing the work to be applied.

        Raises:
            Exception if preparation fails; this will mark the attempt as failed
            without entering a transaction block.
        """
        ...

    def apply(
        self, native_tx_handle: duckdb.DuckDBPyConnection, prepared_work: Any
    ) -> ProcessorResult:
        """
        Apply the prepared work onto the provided native DuckDB transaction handle.

        This runs on the handle's owning worker inside a short transaction.
        The processor MUST write its real projection using this handle.
        The processor MUST NOT open a different native cursor/connection.
        The processor MUST NOT commit or close the supplied handle.
        The processor MUST check source revision, permissions, and idempotency internally.

        Args:
            native_tx_handle: The DuckDB connection currently holding an open transaction.
            prepared_work: The object returned by the `prepare` phase.

        Returns:
            A ProcessorResult detailing the outcome (processed, processed_no_new_fact, etc.)

        Raises:
            Exception if application fails, which will trigger a rollback of the
            current owner transaction encompassing both the projection and receipt.
        """
        ...
