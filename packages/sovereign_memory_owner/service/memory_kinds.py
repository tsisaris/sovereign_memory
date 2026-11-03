"""The plug-in point for new kinds of memory: a kind is a module named in the settings, not code in the runtime.

It is for adding a kind of memory (Letta's archival passages, Zep's summaries, and whatever comes next)
to one of the four memory tiers without editing the runtime. A kind is a class that can rank what it
holds for a question (RankingKind) and, if it stores things, claim the events meant for it and store and
remove them (WritingKind). It takes the settings table kinds.<name>.tier, kinds.<name>.module and
kinds.<name>.class, and gives back the kinds of each tier built and ready. It never imports a module that
the settings do not name, never lets a kind name a tier the settings did not give it, and refuses a
module that lacks the class or the methods its tier needs, by name.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_kinds.py | add a kind of memory by naming a class | Python entry points (importlib.metadata, needs a reinstall of the package for every new kind), stevedore (a plug-in loader on entry points, same reinstall), pluggy (hooks between installed packages), a class named in the settings table (the project's one place for configuration) | A class named in the settings: one importlib call, no install step, and the setting is data that can be changed without a code change |
"""

import importlib
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import duckdb

from service.memory_numbers_services import NumbersServices
from service.memory_processor_seam import RetrievalScope

KIND_FIELDS = ("tier", "module", "class")


@runtime_checkable
class RankingKind(Protocol):
    """A kind of memory that can answer a search: it ranks what it holds by each model's numbers."""

    name: str
    tier: str

    def rank(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        vector_of_model: Any,
        label: str,
    ) -> list[dict[str, Any]]:
        """Return this kind's hits nearest the question by one model's numbers, nearest first.

        Every hit is a dict with event_id, item_id, text and distance. It reads only, and returns
        only what the scope's visibility permits.
        """

    def find(
        self,
        handle: duckdb.DuckDBPyConnection,
        scope: RetrievalScope,
        organization_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return this kind's hits that hold the question's words, for when no model has numbers."""


@runtime_checkable
class WritingKind(RankingKind, Protocol):
    """A kind of memory that stores things: it claims the events meant for it and stores and removes them."""

    def claims(self, envelope: dict[str, Any]) -> bool:
        """Say whether an event is meant for this kind."""

    async def prepare(
        self,
        services: NumbersServices | None,
        envelope: dict[str, Any],
        payload: bytes | None,
    ) -> Any:
        """Read the claimed event into plain data to store, and owe the numbers it needs; no database."""

    def apply(self, store: Any, organization_id: str, actor: str, event_id: str, work: Any) -> bool:
        """Store the prepared work on the owner's open transaction; True when something changed."""

    def remove(self, store: Any, organization_id: str, target_event_id: str) -> bool:
        """Remove what this kind stored for an event; True when something was removed."""


def load_kinds(specs: Mapping[str, Mapping[str, Any]]) -> dict[str, list[RankingKind]]:
    """Build every kind the settings name and group them by tier, in the order of their names.

    Goes in: the kinds settings, name to {tier, module, class}. Comes out: tier to its built kinds.
    Raises ValueError naming the kind when a field is missing, the module cannot be imported, the
    class is not in the module, or the built object does not carry the methods of a ranking kind.
    """
    built: dict[str, list[RankingKind]] = {}
    for name in sorted(specs):
        spec = specs[name]
        missing = [field for field in KIND_FIELDS if not isinstance(spec.get(field), str)]
        if missing:
            raise ValueError(f"kinds.{name} is missing: {', '.join(missing)}")
        try:
            module = importlib.import_module(spec["module"])
            kind = getattr(module, spec["class"])()
        except (ImportError, AttributeError, TypeError) as error:
            raise ValueError(
                f"kinds.{name} cannot be built from {spec['module']}.{spec['class']}: {error}"
            ) from error
        if not isinstance(kind, RankingKind):
            raise ValueError(f"kinds.{name}: {spec['class']} has no rank and find methods")
        if kind.tier != spec["tier"]:
            raise ValueError(
                f"kinds.{name}: the class belongs to tier {kind.tier}, not {spec['tier']}"
            )
        built.setdefault(spec["tier"], []).append(kind)
    return built
