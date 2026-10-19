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

import argparse
import asyncio
import json
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

from .config import TIER_KEYS, ConfigurationError, RuntimeConfig, load

CONFIG_ENVIRONMENT_VARIABLE = "SOVEREIGN_MEMORY_CONFIG"
logger = logging.getLogger(__name__)


def _built(tier: str, make: Callable[[], Any]) -> Any:
    """Return make(), turning a failure to build the engine into a named ConfigurationError."""
    try:
        return make()
    except (ValueError, ImportError, OSError) as error:
        raise ConfigurationError(f"tiers.{tier} cannot be built: {error}") from error


def build_tiers(config: RuntimeConfig, only_tier: str | None = None) -> list[Any]:
    """Build one engine object per configured tier; each is both processor and retriever.

    An engine's package is imported only when its tier is configured, so a
    deployment without a tier (or one whose configuration is invalid) never
    initializes that engine's SDK, logging or telemetry. Raises ConfigurationError
    naming the tier when an engine cannot be built from its settings. It opens no
    database.
    """
    if only_tier is not None and only_tier not in TIER_KEYS:
        raise ConfigurationError(f"Unknown tier {only_tier!r}")
    tiers: list[Any] = []
    if config.episodic_organization is not None and only_tier in (None, "episodic"):
        from service.memory_letta_recall import LettaRecallTier

        organization = config.episodic_organization
        tiers.append(_built("episodic", lambda: LettaRecallTier(organization)))
    if config.temporal_enabled and only_tier in (None, "temporal"):
        from service.memory_graphiti_episodes import GraphitiEpisodeTier

        tiers.append(_built("temporal", GraphitiEpisodeTier))
    if (
        config.relational_embedding is not None
        and config.relational_database_name is not None
        and only_tier in (None, "relational")
    ):
        settings = config.relational_embedding
        database_name = config.relational_database_name

        def relational() -> Any:
            """Build the Cognee embedding engine from the configured model and the chunk tier."""
            from service.memory_cognee_chunks import CogneeChunkTier

            if settings.provider == "agent_embedding":
                from mem0.configs.embeddings.base import BaseEmbedderConfig
                from mem0.embeddings.agent_embedding import AgentEmbedding

                engine = AgentEmbedding(
                    BaseEmbedderConfig(
                        model=settings.model,
                        embedding_dims=settings.dimensions,
                        agent_embedding=settings.agent_embedding,
                    )
                )
            else:
                from cognee.infrastructure.databases.vector.embeddings.LiteLLMEmbeddingEngine import (
                    LiteLLMEmbeddingEngine,
                )

                engine = LiteLLMEmbeddingEngine(
                    model=settings.model,
                    provider=settings.provider,
                    dimensions=settings.dimensions,
                    api_key=settings.api_key,
                    endpoint=settings.endpoint,
                )
            return CogneeChunkTier(engine, database_name, revision=settings.revision)

        tiers.append(_built("relational.embedding", relational))
    if config.personalization is not None and only_tier in (None, "personalization"):
        from mem0.memory.owner_tier import Mem0Tier

        memory = config.personalization
        tiers.append(
            _built(
                "personalization",
                lambda: Mem0Tier(memory, terminal_error=TerminalProcessingError),
            )
        )
    if only_tier is not None and not tiers:
        raise ConfigurationError(f"tiers.{only_tier} is not configured")
    return tiers


def build_service(config: RuntimeConfig, only_tier: str | None = None) -> OwnerService:
    """Return an OwnerService wired to the configured tiers and visibility policy; not started."""
    tiers = build_tiers(config, only_tier)
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
    maintenance task is told to stop and awaited for at most the service's declared
    drain bound, then cancelled (its unfinished receipts stay owed and are retried),
    and the service is stopped, so no task or owner connection outlives the call.
    A failed round is logged by maintenance itself; any other failure of the
    maintenance task, and any cancellation it did not request, is raised from
    here; only the cancellation this call itself requested of a still-running
    maintenance task is suppressed.
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
                _, running = await asyncio.wait(
                    {maintenance}, timeout=service.drain_timeout_seconds
                )
                requested = False
                if running:
                    # A cancellation someone else already asked for stays theirs.
                    requested = maintenance.cancelling() == 0
                    maintenance.cancel()
                    await asyncio.wait({maintenance})
                if not (requested and maintenance.cancelled()):
                    maintenance.result()
            finally:
                await service.stop()
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


async def rebuild(service: OwnerService, tier: str, request_id: str, max_items: int) -> dict:
    """Acquire the Owner normally and run one bounded trusted rebuild, always releasing it."""
    try:
        await service.start(process_pending=False)
        return await service.rebuild_projection(tier, request_id, max_items)
    finally:
        await service.stop()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the runtime from a configuration file; return the process exit status.

    The file is the first argument, else the path in SOVEREIGN_MEMORY_CONFIG.
    An invalid or missing configuration prints the reason and returns 2 before
    anything starts (the service is built, which validates every engine, but not started).
    """
    parser = argparse.ArgumentParser(
        description="Run the configured memory Owner or rebuild one embedding tier"
    )
    parser.add_argument("config", nargs="?")
    parser.add_argument("--rebuild-tier", choices=("relational", "personalization"))
    parser.add_argument("--rebuild-id")
    parser.add_argument("--rebuild-limit", type=int)
    args = parser.parse_args(argv)
    if args.rebuild_tier and (
        not args.rebuild_id or args.rebuild_limit is None or args.rebuild_limit <= 0
    ):
        parser.error("rebuilding requires --rebuild-id and a positive --rebuild-limit")
    if not args.rebuild_tier and (args.rebuild_id is not None or args.rebuild_limit is not None):
        parser.error("rebuild options require --rebuild-tier")
    path = args.config or os.environ.get(CONFIG_ENVIRONMENT_VARIABLE)
    if not path:
        print(f"Give the configuration file or set {CONFIG_ENVIRONMENT_VARIABLE}", file=sys.stderr)
        return 2
    try:
        config = load(path)
        service = build_service(config, args.rebuild_tier)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO)
    if args.rebuild_tier:
        try:
            result = asyncio.run(
                rebuild(service, args.rebuild_tier, args.rebuild_id, args.rebuild_limit)
            )
        except (ValueError, RuntimeError) as error:
            print(f"Rebuild failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(result))
        return 0 if result["status"] == "completed" else 1
    asyncio.run(run(service, config.maintenance_interval_seconds))
    return 0
