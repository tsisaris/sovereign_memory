"""Scoped retrieval through the registered engines, with an owner-side identity filter.

Each tier answers through its own registered retriever. A tier with no
retriever reports "unavailable" and a tier whose retriever raises reports
"failed" with the reason; neither is ever shown as an empty success. Every hit
is checked against the owner's durable events: it must name a captured event
whose producer the principal's resolved visibility permits and that has been
neither removed (expired or forgotten through the owner) nor let expire, or it
is withheld and counted. Removal is recorded when it is captured, so a removed
source is withheld even while a tier has not yet deleted its projection.
"""

import logging
from typing import Any

from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_owner_core import TIERS, OwnerCore
from service.memory_processor_seam import (
    AuthenticatedPrincipal,
    MemoryRetriever,
    RetrievalScope,
    VisibilityPolicy,
)
from service.memory_visibility import RestrictedPolicy

logger = logging.getLogger(__name__)


class MemoryRetrievalService:
    """Answer scoped retrieval requests across the registered tiers."""

    def __init__(
        self,
        conn: AsyncConnection,
        retrievers: dict[str, MemoryRetriever],
        policy: VisibilityPolicy | None = None,
    ) -> None:
        """Bind the owner connection, the retrievers keyed by tier name and the visibility policy.

        Without a policy every principal is restricted to its own events.
        """
        self.conn = conn
        self.core = OwnerCore(conn)
        self.retrievers = retrievers
        self.policy = policy or RestrictedPolicy()

    async def retrieve(
        self, principal: AuthenticatedPrincipal, query: str, limit: int
    ) -> dict[str, dict[str, Any]]:
        """Return tier -> {"status", "hits", "withheld"/"reason"} for the authenticated principal.

        The scope comes only from the principal. It never writes and never
        returns another actor's hits.
        """
        visibility = await self.policy.resolve(principal)
        scope = RetrievalScope(
            agent_id=principal.agent_id, scope=principal.scope, visibility=visibility
        )
        answers: dict[str, dict[str, Any]] = {}
        for tier in TIERS:
            retriever = self.retrievers.get(tier)
            if retriever is None:
                answers[tier] = {
                    "status": "unavailable",
                    "reason": "no retriever registered for this tier",
                    "hits": [],
                }
                continue
            try:
                prepared = await retriever.prepare_query(scope, query, limit)
                hits = await self.conn.run(retriever.retrieve, self.conn.native, scope, prepared)
                answers[tier] = await self._filter(tier, hits, scope)
            except Exception as error:
                logger.exception("Retrieval for tier %s failed", tier)
                answers[tier] = {
                    "status": "failed",
                    "reason": f"{type(error).__name__}: {error}",
                    "hits": [],
                }
        return answers

    async def _filter(
        self, tier: str, hits: list[dict[str, Any]], scope: RetrievalScope
    ) -> dict[str, Any]:
        """Keep hits derived from live events the scope's visibility permits and count the rest.

        Raises ValueError when a hit is not a dict naming its event_id: an
        engine that cannot attribute a hit cannot be trusted with it.
        """
        for hit in hits:
            if not isinstance(hit, dict) or not isinstance(hit.get("event_id"), str):
                raise ValueError(f"{tier} returned a hit without an event_id")
        producers = await self.core.live_producers([hit["event_id"] for hit in hits])
        kept = [
            hit
            for hit in hits
            if hit["event_id"] in producers
            and scope.visibility.permits(producers[hit["event_id"]], scope.agent_id)
        ]
        return {"status": "ok", "hits": kept, "withheld": len(hits) - len(kept)}
