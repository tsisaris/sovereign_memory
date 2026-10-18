"""The composed Owner runtime: configured tier engines registered with the OwnerService.

build_service wires the engines the configuration names to the one owner
service: Letta recall (episodic), Graphiti episodes (temporal), Cognee chunks
(relational) and Mem0 (personalization), each registered as both processor and
retriever. A tier the file does not configure gets no processor, so its
receipts stay pending and its retrieval reports unavailable; nothing is
simulated. run serves until stopped and runs the owner's scheduled work
(retrying owed receipts and expiring events) at the configured interval. It
opens the database only through the OwnerService and never calls a model
itself.
"""

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable, Sequence
from typing import Any

from service.memory_dispatcher import ProcessorRegistry
from service.memory_owner_service import OwnerService
from service.memory_processor_seam import TerminalProcessingError
from service.memory_visibility import RestrictedPolicy, ScopeLabelPolicy

from .config import ConfigurationError, RuntimeConfig, load

CONFIG_ENVIRONMENT_VARIABLE = "SOVEREIGN_MEMORY_CONFIG"
logger = logging.getLogger(__name__)


def _built(tier: str, make: Callable[[], Any]) -> Any:
    """Return make(), turning a failure to build the engine into a named ConfigurationError."""
    try:
        return make()
    except (ValueError, ImportError, OSError) as error:
        raise ConfigurationError(f"tiers.{tier} cannot be built: {error}") from error


def build_tiers(config: RuntimeConfig) -> list[Any]:
    """Build one engine object per configured tier; each is both processor and retriever.

    An engine's package is imported only when its tier is configured, so a
    deployment without a tier (or one whose configuration is invalid) never
    initializes that engine's SDK, logging or telemetry. Raises ConfigurationError
    naming the tier when an engine cannot be built from its settings. It opens no
    database.
    """
    tiers: list[Any] = []
    if config.episodic_organization is not None:
        from service.memory_letta_recall import LettaRecallTier

        organization = config.episodic_organization
        tiers.append(_built("episodic", lambda: LettaRecallTier(organization)))
    if config.temporal_enabled:
        from service.memory_graphiti_episodes import GraphitiEpisodeTier

        tiers.append(_built("temporal", GraphitiEpisodeTier))
    if config.relational_embedding is not None and config.relational_database_name is not None:
        settings = config.relational_embedding
        database_name = config.relational_database_name

        def relational() -> Any:
            """Build the Cognee embedding engine from the configured model and the chunk tier."""
            from cognee.infrastructure.databases.vector.embeddings.LiteLLMEmbeddingEngine import (
                LiteLLMEmbeddingEngine,
            )
            from service.memory_cognee_chunks import CogneeChunkTier

            engine = LiteLLMEmbeddingEngine(
                model=settings.model,
                provider=settings.provider,
                dimensions=settings.dimensions,
                api_key=settings.api_key,
                endpoint=settings.endpoint,
            )
            return CogneeChunkTier(engine, database_name)

        tiers.append(_built("relational.embedding", relational))
    if config.personalization is not None:
        from mem0.memory.owner_tier import Mem0Tier

        memory = config.personalization
        tiers.append(
            _built(
                "personalization",
                lambda: Mem0Tier(memory, terminal_error=TerminalProcessingError),
            )
        )
    return tiers


def build_service(config: RuntimeConfig) -> OwnerService:
    """Return an OwnerService wired to the configured tiers and visibility policy; not started."""
    tiers = build_tiers(config)
    registry = ProcessorRegistry()
    for tier in tiers:
        registry.register(tier)
    policy = (
        ScopeLabelPolicy(config.pool_scope_labels)
        if config.pool_scope_labels
        else RestrictedPolicy()
    )
    return OwnerService(
        config.socket_path,
        config.database_path,
        registry=registry,
        retrievers={tier.tier_name: tier for tier in tiers},
        policy=policy,
        max_request_bytes=config.max_request_bytes,
        drain_timeout_seconds=config.drain_timeout_seconds,
    )


async def maintain(service: OwnerService, interval: float, stop: asyncio.Event) -> None:
    """Every interval seconds, expire elapsed events and retry owed receipts, until stop is set.

    A failed round is logged with its traceback and the next round still runs.
    """
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            await service.enforce_expiry()
            await service.process_owed()
        except Exception:
            logger.exception("Scheduled owner maintenance failed")


async def run(service: OwnerService, maintenance_interval_seconds: float) -> None:
    """Start the service and serve until SIGINT or SIGTERM, then stop it cleanly.

    Whatever ends the wait (a signal, a failed start, cancellation), the
    maintenance task is told to stop and awaited, and the service is stopped, so no
    task or owner connection outlives the call. A failure of maintenance itself
    is logged by it, never lost.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await service.start()
        maintenance = asyncio.create_task(maintain(service, maintenance_interval_seconds, stop))
        try:
            await stop.wait()
        finally:
            stop.set()
            try:
                await maintenance
            finally:
                await service.stop()
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the runtime from a configuration file; return the process exit status.

    The file is the first argument, else the path in SOVEREIGN_MEMORY_CONFIG.
    An invalid or missing configuration prints the reason and returns 2 before
    anything starts (the service is built, which validates every engine, but not started).
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    path = arguments[0] if arguments else os.environ.get(CONFIG_ENVIRONMENT_VARIABLE)
    if not path:
        print(f"Give the configuration file or set {CONFIG_ENVIRONMENT_VARIABLE}", file=sys.stderr)
        return 2
    try:
        config = load(path)
        service = build_service(config)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(service, config.maintenance_interval_seconds))
    return 0
