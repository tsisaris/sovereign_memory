"""Scoped retrieval through the registered engines, merged across models and filtered by identity.

Each tier answers through its own registered retriever. A tier with no
retriever reports "unavailable" and a tier whose retriever raises reports
"failed" with the reason; neither is ever shown as an empty success. A tier that
ranks by models' numbers returns one ranking per fresh model, each made only from
that model's own numbers; the current combiner (reciprocal rank fusion at first)
merges them into the tier's answer, which reports the combiner that produced it,
the models used, the models ranked but not yet in the combiner, and each model
left out with its reason. A stale model is never treated as an empty result.
Hits roll up to their events. Every hit is checked against the owner's durable
events: it must name a captured event whose producer the principal's resolved
visibility permits and that has been neither removed (expired or forgotten
through the owner) nor let expire, or it is withheld and counted. Removal is
recorded when it is captured, so a removed source is withheld even while a tier
has not yet deleted its projection. When two or more models' first answers
differ, the search records the disagreement as a ground-truth case.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_combiners import (
    RULE_ARTIFACT,
    RULE_RRF,
    ArtifactCombiner,
    CombinerRow,
    current_combiner,
    reciprocal_rank_fusion,
)
from service.memory_ground_truth import COMBINED_OUTPUT, MODEL_OUTPUT, record_case, record_output
from service.memory_owner_core import TIERS, OwnerCore
from service.memory_processor_seam import (
    AuthenticatedPrincipal,
    MemoryRetriever,
    RetrievalScope,
    VisibilityPolicy,
)
from service.memory_review import disagree
from service.memory_settings import SearchSettings
from service.memory_visibility import RestrictedPolicy

logger = logging.getLogger(__name__)


class MemoryRetrievalService:
    """Answer scoped retrieval requests across the registered tiers."""

    def __init__(
        self,
        conn: AsyncConnection,
        retrievers: dict[str, MemoryRetriever],
        policy: VisibilityPolicy | None = None,
        settings: SearchSettings | None = None,
        artifacts: dict[str, ArtifactCombiner] | None = None,
    ) -> None:
        """Bind the owner connection, the retrievers keyed by tier name and the visibility policy.

        Without a policy every principal is restricted to its own events. settings
        are the declared search settings (needed to merge rankings) and artifacts
        the trained combiners the builder supplied, keyed by combiner id.
        """
        self.conn = conn
        self.core = OwnerCore(conn)
        self.retrievers = retrievers
        self.policy = policy or RestrictedPolicy()
        self.settings = settings
        self.artifacts = artifacts or {}

    async def retrieve(
        self, principal: AuthenticatedPrincipal, query: str, limit: int
    ) -> dict[str, dict[str, Any]]:
        """Return tier -> {"status", "hits", "withheld"/"reason", ...} for the authenticated principal.

        A tier that ranks by models also reports "combiner", "models" (used,
        ranked_not_combined, left_out) and "settings_used". The scope comes only
        from the principal. Apart from recording a disagreement case it never
        writes, and it never returns another actor's hits.
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

                def ready(tier=tier):
                    """Refuse partial rebuilds on the same worker that performs retrieval."""
                    handle = self.conn.native
                    active = handle.execute(
                        "SELECT request_id FROM memory_rebuilds WHERE tier = ? "
                        "AND status IN ('processing', 'failed')",
                        [tier],
                    ).fetchone()
                    if active is not None:
                        raise RuntimeError(f"Projection rebuild {active[0]} is not complete")

                await self.conn.run(ready)
                prepared = await retriever.prepare_query(scope, query, limit)

                def read(ready=ready, retriever=retriever, prepared=prepared):
                    """Check rebuilding again after preparation, before the actual index read."""
                    ready()
                    return retriever.retrieve(self.conn.native, scope, prepared)

                result = await self.conn.run(read)
                if isinstance(result, list):
                    answers[tier] = await self._filter(tier, result, scope)
                    answers[tier]["combiner"] = None
                    answers[tier]["combiner_note"] = "this memory kind ranks by no model's numbers"
                else:
                    answers[tier] = await self._merge(tier, result, scope, principal, query, limit)
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

    async def _merge(
        self,
        tier: str,
        result: Any,
        scope: RetrievalScope,
        principal: AuthenticatedPrincipal,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        """Merge a tier's per-model rankings with the current combiner and report how.

        Each ranking is filtered by the same identity checks as any hit, then the
        combiner named as current merges the rankings of the models it uses.
        The answer says which models were used, which were ranked but are not in
        the combiner, and which were left out and why; when no model could rank
        the status is "unavailable", never an empty success. Hits roll up to
        events. Raises RuntimeError when no search settings were declared.
        """
        if self.settings is None:
            raise RuntimeError("Merging rankings needs the declared search settings")
        kept: dict[str, list[dict[str, Any]]] = {}
        withheld = 0
        for label, hits in result.rankings.items():
            filtered = await self._filter(tier, hits, scope)
            kept[label] = filtered["hits"]
            withheld += filtered["withheld"]
        left_out = dict(result.left_out)
        combiner = await self.conn.run(lambda: current_combiner(self.conn.native))
        settings_used = self.settings.report()
        base = {"withheld": withheld, "settings_used": settings_used}
        if combiner is None:
            return {
                **base,
                "status": "unavailable",
                "reason": "no combiner is current: no embedding model is registered",
                "hits": [],
                "combiner": None,
                "models": {"used": [], "ranked_not_combined": [], "left_out": left_out},
            }
        used = [label for label in combiner.models if label in kept]
        for label in combiner.models:
            if label not in kept and label not in left_out:
                left_out[label] = "the tier returned no ranking for this model"
        models = {
            "used": used,
            "ranked_not_combined": sorted(label for label in kept if label not in combiner.models),
            "left_out": left_out,
        }
        if not used:
            return {
                **base,
                "status": "unavailable",
                "reason": "no model of the current combiner could rank this question",
                "hits": [],
                "combiner": combiner.describe(),
                "models": models,
            }
        merged = await self._combine(combiner, kept, used)
        events = self._roll_up(merged, kept, used)
        case_id, error = await self._record_disagreement(
            tier, principal, query, combiner, kept, merged
        )
        answer = {
            **base,
            "status": "ok" if len(used) == len(combiner.models) else "partial",
            "hits": events[:limit],
            "combiner": combiner.describe(),
            "models": models,
            "ground_truth_case": case_id,
        }
        if error is not None:
            answer["ground_truth_error"] = error
        return answer

    async def _combine(
        self, combiner: CombinerRow, kept: dict[str, list[dict[str, Any]]], used: list[str]
    ) -> list[tuple[str, float]]:
        """Return (item id, score) best first from the combiner's rule over the used rankings.

        Reciprocal rank fusion is computed here. A trained artifact is asked through
        the supplied object, never run by memory; its order is kept and its scores
        are the rank positions' reciprocals only for reporting. Raises RuntimeError
        for an artifact combiner whose artifact was not supplied.
        """
        rankings = {label: [h["item_id"] for h in kept[label]] for label in used}
        if combiner.rule == RULE_RRF:
            if self.settings is None:
                raise RuntimeError("Merging rankings needs the declared search settings")
            return reciprocal_rank_fusion(rankings, self.settings.merging_constant)
        if combiner.rule == RULE_ARTIFACT:
            artifact = self.artifacts.get(combiner.combiner_id)
            if artifact is None:
                raise RuntimeError(
                    f"The artifact for combiner {combiner.combiner_id} is not supplied"
                )
            order = (await artifact.merge([{"case_id": None, "rankings": rankings}]))[None]
            return [(item, 1.0 / rank) for rank, item in enumerate(order, start=1)]
        raise RuntimeError(f"Unknown combiner rule {combiner.rule!r}")

    @staticmethod
    def _roll_up(
        merged: list[tuple[str, float]], kept: dict[str, list[dict[str, Any]]], used: list[str]
    ) -> list[dict[str, Any]]:
        """Group merged items by their event, best first; each event keeps its items and best score.

        An item's hit is the one the first used model that ranked it gave, so the
        event hit shows real text from a real hit and nothing is invented.
        """
        by_item: dict[str, dict[str, Any]] = {}
        for label in used:
            for hit in kept[label]:
                by_item.setdefault(hit["item_id"], hit)
        events: dict[str, dict[str, Any]] = {}
        for item_id, score in merged:
            hit = by_item[item_id]
            event = events.setdefault(
                hit["event_id"],
                {"event_id": hit["event_id"], "score": score, "items": []},
            )
            event["items"].append({**hit, "score": score})
        return list(events.values())

    async def _record_disagreement(
        self,
        tier: str,
        principal: AuthenticatedPrincipal,
        query: str,
        combiner: CombinerRow,
        kept: dict[str, list[dict[str, Any]]],
        merged: list[tuple[str, float]],
    ) -> tuple[str | None, str | None]:
        """Record a ground-truth case when the models' first answers differ; return (case id, error).

        The case id is derived from the question, the rankings and the combiner,
        so asking the same thing twice records one case. The case points to the
        events involved and copies none of their text. A failure to record is
        returned, never raised or hidden, so the search itself still answers.
        """

        settings = self.settings
        if settings is None:
            raise RuntimeError("Recording a case needs the declared search settings")

        def output(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Shape a ranking for the ground-truth record: ids and native scores, no text."""
            return [
                {
                    "item_id": h["item_id"],
                    "event_id": h["event_id"],
                    "score": h.get("distance", h.get("score")),
                }
                for h in hits
            ]

        outputs = [
            {"key": label, "role": MODEL_OUTPUT, "output": output(hits)}
            for label, hits in sorted(kept.items())
        ]
        if not disagree(outputs):
            return None, None
        involved = sorted({h["event_id"] for hits in kept.values() for h in hits})
        digest = hashlib.sha256(
            json.dumps(
                [
                    tier,
                    query,
                    combiner.combiner_id,
                    settings.merging_constant,
                    {label: [h["item_id"] for h in hits] for label, hits in sorted(kept.items())},
                ],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        case_id = f"case-{digest}"
        now = datetime.now(timezone.utc).isoformat()
        item_event = {h["item_id"]: h["event_id"] for hits in kept.values() for h in hits}
        combined = [
            {"item_id": item, "event_id": item_event[item], "score": score}
            for item, score in merged
        ]

        def write() -> None:
            """Record the case and every output in one transaction on the owner's worker."""
            handle = self.conn.native
            handle.begin()
            try:
                record_case(
                    handle,
                    case_id=case_id,
                    kind="model_choice",
                    memory_kind=tier,
                    input_kind="question",
                    input_value=query,
                    events=involved,
                    created_at=now,
                    created_by=principal.agent_id,
                    held_back_share=settings.held_back_share,
                )
                for entry in outputs:
                    model_id, revision = entry["key"].rsplit("@", 1)
                    record_output(
                        handle,
                        case_id=case_id,
                        role=MODEL_OUTPUT,
                        model_id=model_id,
                        revision=revision,
                        output=entry["output"],
                        combiner_id=combiner.combiner_id,
                        recorded_at=now,
                    )
                record_output(
                    handle,
                    case_id=case_id,
                    role=COMBINED_OUTPUT,
                    model_id=None,
                    revision=None,
                    output=combined,
                    combiner_id=combiner.combiner_id,
                    recorded_at=now,
                )
                handle.commit()
            except BaseException:
                handle.rollback()
                raise

        try:
            await self.conn.run(write)
        except ValueError as error:
            logger.exception("Recording the disagreement case failed")
            return None, f"{type(error).__name__}: {error}"
        return case_id, None
