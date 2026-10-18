"""Minimal service-side processor contract for sovereign memory engines.

This defines the interface that each downstream engine (Letta, Zep/Graphiti, Cognee, Mem0)
must implement to participate in the unified Owner Service transaction lifecycle.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, TypeVar

import duckdb

T = TypeVar("T")

# Runs a synchronous function on the owner's worker with the owner's native
# handle, outside any write transaction, and returns what the function returns.
OwnerReader = Callable[[Callable[[duckdb.DuckDBPyConnection], T]], Awaitable[T]]


class TerminalProcessingError(Exception):
    """Raised by a processor when retrying the same event can never succeed.

    The dispatcher records a terminal_failure receipt for it. Any other
    exception is recorded as a retryable_failure. It carries only a message.
    """


@dataclass(frozen=True)
class Visibility:
    """Which captured events an authenticated principal may see, resolved by a policy.

    mode "own": only events the principal produced (the fail-closed default).
    mode "pool": every event in the shared pool (the Sovereign universal pool).
    mode "granted": events produced by any actor in actor_agent_ids, as granted
    by a permission owner. It is data about authorization, never a request.
    """

    mode: str
    actor_agent_ids: frozenset[str] = frozenset()

    def permits(self, event_actor: str | None, principal_id: str) -> bool:
        """Say whether an event produced by event_actor is visible to principal_id.

        An unknown mode or a missing actor permits nothing.
        """
        if event_actor is None:
            return False
        if self.mode == "own":
            return event_actor == principal_id
        if self.mode == "pool":
            return True
        if self.mode == "granted":
            return event_actor in self.actor_agent_ids
        return False


@dataclass(frozen=True)
class RetrievalScope:
    """The authenticated identity and visibility a retrieval is allowed to use.

    agent_id is the transport-authenticated principal (provenance); scope is the
    authenticator's scope label; visibility is what the policy resolved from
    them. Engines must restrict every read, cache and traversal to events the
    visibility permits. It never carries anything the caller merely claimed.
    """

    agent_id: str
    scope: str
    visibility: Visibility


class VisibilityPolicy(Protocol):
    """The seam through which a permission owner decides what a principal may see."""

    async def resolve(self, principal: "AuthenticatedPrincipal") -> Visibility:
        """Return the principal's visibility, or raise PermissionError to refuse the request (fail closed)."""
        ...


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
    """The required contract for memory engines to process events safely.

    Besides ordinary captured events, a processor receives owner-authored
    envelopes whose event_type is "system.expire": a fully valid v2 event whose
    causation_event_id names the captured event whose projection must be removed
    and whose stored payload is JSON naming the operation. The processor removes
    its projection in apply, atomically with the receipt, or fails truthfully.

    A processor may also define attach_reader(reader), called once at
    registration with an OwnerReader, for reads its prepare step needs. It must
    use that reader instead of opening any connection of its own.
    """

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


class MemoryRetriever(Protocol):
    """The contract for engines that answer scoped retrieval.

    It mirrors the write seam: model work happens outside the owner worker,
    reading happens on the owner's handle.
    """

    @property
    def tier_name(self) -> str:
        """Return the tier identity this retriever answers for."""
        ...

    async def prepare_query(self, scope: RetrievalScope, query: str, limit: int) -> Any:
        """Do model work for the query (such as embedding) outside any database job."""
        ...

    def retrieve(
        self,
        native_handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        prepared_query: Any,
    ) -> List[Dict[str, Any]]:
        """Read hits on the owner's handle; never write, commit, close or open another connection.

        Every hit must be a dict carrying the string key "event_id" naming the
        captured event it derives from, so the owner can verify identity and expiry.
        """
        ...


class AuthenticatedPrincipal:
    """Represents a trusted actor authenticated at the transport layer."""

    def __init__(self, agent_id: str, scope: str):
        """Initialize authenticated principal."""
        self.agent_id = agent_id
        self.scope = scope


class ConnectionAuthenticator(Protocol):
    """A platform-specific seam for extracting trusted peer credentials from a socket."""

    async def authenticate_stream(self, reader: Any, writer: Any) -> AuthenticatedPrincipal:
        """Extract and verify the principal from the transport layer securely."""
        ...
