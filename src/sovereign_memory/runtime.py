"""The composed Owner runtime: configured tier engines registered with the OwnerService.

build_service wires the engines the configuration names to the one owner
service: Letta recall (episodic), Graphiti episodes (temporal), Cognee chunks
(relational) and Mem0 (personalization), each registered as both processor and
retriever; the declared search settings and the models to register go to the Owner. A tier the file does not configure gets no processor, so its
receipts stay pending and its retrieval reports unavailable; nothing is
simulated. run serves until stopped and runs the owner's scheduled work
(retrying owed receipts and expiring events) at the configured interval. It
opens the database only through the OwnerService (after the brief read-only
read of the settings table) and never calls a model itself.
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
from service.memory_owner_core import OwnerCore
from service.memory_owner_service import OwnerService
from service.memory_processor_seam import TerminalProcessingError
from service.memory_supplier_http import CorpusHttpSupplier
from service.memory_tier_support import event_text
from service.memory_visibility import RestrictedPolicy, ScopeLabelPolicy

from .config import (
    SET_COMMAND,
    TIER_KEYS,
    ConfigurationError,
    RuntimeConfig,
    load,
)
from .settings_store import (
    change_settings,
    parse_assignment,
    parse_description,
    show_page,
)

DATABASE_ENVIRONMENT_VARIABLE = "SOVEREIGN_MEMORY_DATABASE"
OLD_CONFIG_ENVIRONMENT_VARIABLE = "SOVEREIGN_MEMORY_CONFIG"
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
    if config.relational_database_name is not None and only_tier in (None, "relational"):
        database_name = config.relational_database_name
        embedding_model = config.relational_embedding_model

        def relational() -> Any:
            """Build the relational tier: one Cognee chunk per supplied piece, no model of its own."""
            from service.memory_cognee_chunks import CogneeChunkTier

            return CogneeChunkTier(database_name, embedding_model=embedding_model)

        tiers.append(_built("relational", relational))
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


def model_parts_factory(config: RuntimeConfig, tiers: list[Any]) -> Callable[[Any], Any] | None:
    """Return the function that builds the language-model router, or None when no model is set.

    It takes the validated configuration and the built tiers. The function it returns takes
    the service's database access and builds one part per configured tier, every one made
    from the single language_model setting and nothing else. It calls no model and opens no
    database.
    """
    language_model = config.language_model
    if language_model is None:
        return None

    def make(database: Any) -> Any:
        """Build the router with a part for each configured tier."""
        from service.memory_model_parts import ModelParts

        parts: list[Any] = []
        if config.temporal_enabled:
            from service.memory_model_graphiti import GraphitiModel

            parts.append(GraphitiModel(language_model, database))
        if config.cognee is not None:
            from service.memory_model_cognee import CogneeModel

            parts.append(CogneeModel(language_model, config.cognee))
        if config.letta is not None and config.episodic_organization is not None:
            from service.memory_model_letta import LettaModel

            parts.append(
                LettaModel(language_model, database, config.letta, config.episodic_organization)
            )
        if config.zep is not None:
            from service.memory_model_zep import ZepModel

            parts.append(ZepModel(language_model, database, config.zep))
        for tier in tiers:
            if tier.tier_name == "personalization":
                from .config import _mem0_llm
                from .model_mem0 import Mem0Model

                parts.append(
                    Mem0Model(tier, lambda model: _mem0_llm({**language_model, "model": model}))
                )
        return ModelParts(language_model, parts)

    return make


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
    holder: list[OwnerService] = []
    supplier = None
    if config.supplier is not None:

        async def original_text(event_id: str) -> str | None:
            """Return the original text of one of this service's events, read from its own file."""
            envelope, payload = await OwnerCore(holder[0].lifetime_conn).load_event(event_id)
            return event_text(envelope, payload)[0]

        supplier = CorpusHttpSupplier(
            config.supplier["base_url"],
            config.supplier["requester_agent_id"],
            float(config.supplier["timeout_seconds"]),
            original_text,
        )
    try:
        service = OwnerService(
            config.socket_path,
            config.database_path,
            registry=registry,
            retrievers={tier.tier_name: tier for tier in tiers},
            policy=policy,
            max_request_bytes=config.max_request_bytes,
            drain_timeout_seconds=config.drain_timeout_seconds,
            ranking=config.ranking,
            models=config.models,
            supplier=supplier,
            model_parts_factory=model_parts_factory(config, tiers),
        )
    except ValueError as error:
        raise ConfigurationError(f"ranking: {error}") from error
    holder.append(service)
    return service


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
            await service.sync_numbers()
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


def set_setting_command(argv: Sequence[str]) -> int:
    """Change one or several settings in the memory database and print one line per change.

    Takes the memory database path, any number of name=value pairs (values are JSON,
    or plain text when not JSON), --describe name=words, --unset name and --by who.
    Returns 0 after printing the changes, or prints the reason and returns 2 with
    nothing changed. It never starts the service.
    """
    parser = argparse.ArgumentParser(
        prog="python -m sovereign_memory set-setting",
        description="Check and change settings in the memory database; records old value, "
        "new value, when and who",
    )
    parser.add_argument("database")
    parser.add_argument("assignments", nargs="*", metavar="name=value")
    parser.add_argument("--describe", action="append", default=[], metavar="name=words")
    parser.add_argument("--unset", action="append", default=[], metavar="name")
    parser.add_argument("--by", metavar="who", help="who is changing it (default: the login name)")
    args = parser.parse_intermixed_args(argv)
    try:
        assignments: dict[str, Any] = {}
        for text in args.assignments:
            name, value = parse_assignment(text)
            if name in assignments:
                raise ConfigurationError(f"{name} is given twice")
            assignments[name] = value
        descriptions: dict[str, str] = {}
        for text in args.describe:
            name, words = parse_description(text)
            descriptions[name] = words
        lines = change_settings(args.database, assignments, descriptions, args.unset, args.by)
    except ConfigurationError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


def show_settings_command(argv: Sequence[str]) -> int:
    """Print the readable settings page of a memory database, built from the database each run.

    Takes the memory database path and optionally --history. Returns 0 after printing,
    or prints the reason and returns 2. It only reads.
    """
    parser = argparse.ArgumentParser(
        prog="python -m sovereign_memory show-settings",
        description="Show every setting: name, value, what it does, when last changed and by whom",
    )
    parser.add_argument("database")
    parser.add_argument("--history", action="store_true", help="also list every recorded change")
    args = parser.parse_args(argv)
    try:
        page = show_page(args.database, args.history)
    except ConfigurationError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    print(page)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the runtime on a memory database, or change or show its settings; return the exit status.

    `set-setting <database> name=value ...` changes settings and `show-settings <database>` prints them. Otherwise the memory
    database is the first argument, else the path in SOVEREIGN_MEMORY_DATABASE, and
    every other setting is read from its settings table. The old SOVEREIGN_MEMORY_CONFIG
    variable, and a TOML file given where the database belongs, are refused with a
    message naming the new way. An invalid or missing setting prints the reason and
    returns 2 before anything starts (the service is built, which validates every
    engine, but not started).
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "set-setting":
        return set_setting_command(arguments[1:])
    if arguments and arguments[0] == "show-settings":
        return show_settings_command(arguments[1:])
    if os.environ.get(OLD_CONFIG_ENVIRONMENT_VARIABLE):
        print(
            f"{OLD_CONFIG_ENVIRONMENT_VARIABLE} is no longer read: settings now live in the "
            f"memory database. Set {DATABASE_ENVIRONMENT_VARIABLE} to the database file, and "
            f"set settings with: {SET_COMMAND}",
            file=sys.stderr,
        )
        return 2
    parser = argparse.ArgumentParser(
        description="Run the memory Owner on a memory database or rebuild one embedding tier"
    )
    parser.add_argument("database", nargs="?")
    parser.add_argument("--rebuild-tier", choices=("relational", "personalization"))
    parser.add_argument("--rebuild-id")
    parser.add_argument("--rebuild-limit", type=int)
    args = parser.parse_args(arguments)
    if args.rebuild_tier and (
        not args.rebuild_id or args.rebuild_limit is None or args.rebuild_limit <= 0
    ):
        parser.error("rebuilding requires --rebuild-id and a positive --rebuild-limit")
    if not args.rebuild_tier and (args.rebuild_id is not None or args.rebuild_limit is not None):
        parser.error("rebuild options require --rebuild-tier")
    path = args.database or os.environ.get(DATABASE_ENVIRONMENT_VARIABLE)
    if not path:
        print(
            f"Give the memory database file or set {DATABASE_ENVIRONMENT_VARIABLE}",
            file=sys.stderr,
        )
        return 2
    if path.endswith(".toml"):
        print(
            f"{path} looks like a settings file, but settings now live in the memory "
            f"database. Set them with: {SET_COMMAND}",
            file=sys.stderr,
        )
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
