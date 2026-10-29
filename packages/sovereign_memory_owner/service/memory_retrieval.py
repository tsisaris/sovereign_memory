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
differ, or the question has a known answer, the search records the run as a ground-truth case
holding every reader's ranking, the keyword, merged and second-stage orders and the settings.
"""

import hashlib
import json
import logging
from contextlib import nullcontext
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
from service.memory_context_assembler import ContextAssembler
from service.memory_ground_truth import (
    COMBINED_OUTPUT,
    MODEL_OUTPUT,
    RERANKED_OUTPUT,
    WORDS_OUTPUT,
    latest_known,
    record_case,
    record_output,
    record_run,
)
from service.memory_numbers_services import NumbersServices
from service.memory_owner_core import TIERS, OwnerCore
from service.memory_processor_seam import (
    AuthenticatedPrincipal,
    MemoryRetriever,
    RetrievalScope,
    VisibilityPolicy,
)
from service.memory_review import disagree
from service.memory_settings import SearchSettings
from service.memory_supplier import NumbersNotYetSupplied
from service.memory_visibility import RestrictedPolicy

logger = logging.getLogger(__name__)


def item_kind(hit: dict[str, Any]) -> str:
    """Name what kind of thing a hit is ("fact", "memory", "episode" or "other"); pure.

    It takes a tier's hit and reads only which keys it carries. It returns the kind so the
    ground-truth record can tell a Graphiti fact from a Mem0 memory or an episode. It never
    reads the hit's text.
    """
    if "fact" in hit:
        return "fact"
    if "memory" in hit:
        return "memory"
    if "episode_uuid" in hit:
        return "episode"
    return "other"


class MemoryRetrievalService:
    """Answer scoped retrieval requests across the registered tiers."""

    def __init__(
        self,
        conn: AsyncConnection,
        retrievers: dict[str, MemoryRetriever],
        policy: VisibilityPolicy | None = None,
        settings: SearchSettings | None = None,
        artifacts: dict[str, ArtifactCombiner] | None = None,
        numbers: NumbersServices | None = None,
    ) -> None:
        """Bind the owner connection, the retrievers keyed by tier name and the visibility policy.

        Without a policy every principal is restricted to its own events. settings
        are the declared search settings (needed to merge rankings) and artifacts
        the trained combiners the builder supplied, keyed by combiner id. numbers is
        the supplier's door, used only for the keyword ranking when the settings
        declare words_capability.
        """
        self.conn = conn
        self.core = OwnerCore(conn)
        self.retrievers = retrievers
        self.policy = policy or RestrictedPolicy()
        self.settings = settings
        self.artifacts = artifacts or {}
        self.numbers = numbers

    async def retrieve(
        self, principal: AuthenticatedPrincipal, query: str, limit: int
    ) -> dict[str, dict[str, Any]]:
        """Return tier -> {"status", "hits", "withheld"/"reason", ...} for the authenticated principal.

        A tier that ranks by models also reports "combiner", "models" (used,
        ranked_not_combined, left_out), "settings_used", the merged order, the second
        stage ("rerank") and the facts it held back as no longer true ("not_current").
        The question's numbers are fetched once per model for all tiers. The scope comes
        only from the principal. Apart from recording a disagreement case it never
        writes, and it never returns another actor's hits.
        """
        return (await self.retrieve_many(principal, [query], limit))[0]

    async def retrieve_many(
        self, principal: AuthenticatedPrincipal, queries: list[str], limit: int
    ) -> list[dict[str, dict[str, Any]]]:
        """Answer several questions, asking each model for all their numbers in one request.

        Goes in: the principal, the questions and the limit. Comes out: one answer per question, in
        order, each as retrieve describes. Each model's numbers for every question are fetched first,
        once per model, so a supplier that loads a model when asked loads it once for the whole
        batch; the second-stage scorer is asked after, question by question. It never answers one
        principal's question with another's scope.
        """
        context = (
            nullcontext() if self.numbers is None else self.numbers.question_batch(list(queries))
        )
        async with context:
            return [await self._retrieve_one(principal, query, limit) for query in queries]

    async def _retrieve_one(
        self, principal: AuthenticatedPrincipal, query: str, limit: int
    ) -> dict[str, dict[str, Any]]:
        """Answer one question tier by tier, using numbers the batch already fetched."""
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
        the status is "unavailable", never an empty success. The answer also lists
        each used model's own order ("rankings") beside the merged hits. Hits roll
        up to events. Raises RuntimeError when no search settings were declared.
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
        if self.settings.model_weights is not None:
            for label in used:
                if label not in self.settings.model_weights:
                    left_out[label] = (
                        "no weight is declared for this model in ranking.model_weights"
                    )
            used = [label for label in used if label in self.settings.model_weights]
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
        words_report: dict[str, Any] = {
            "applied": False,
            "reason": "no words_capability is declared",
        }
        words: list[str] | None = None
        if self.settings.words_capability is not None and combiner.rule == RULE_RRF:
            words, words_report = await self._words_ranking(query, kept, used)
        merged = await self._combine(combiner, kept, used, words)
        decay = {"applied": False, "reason": "no decay_time_constant_seconds is declared"}
        if self.settings.decay_time_constant_seconds is not None:
            merged, decay = await self._apply_decay(merged, kept, used)
        first_stage = merged
        rerank: dict[str, Any] = {"applied": False, "reason": "no rerank_capability is declared"}
        if self.settings.rerank_capability is not None:
            merged, rerank = await self._rerank(query, merged, kept, used)
        events = self._roll_up(merged, kept, used)
        stages = {
            "merged": first_stage,
            "final": merged,
            "words": words,
            "rerank": rerank,
            "report": {
                "models": models,
                "words": words_report,
                "decay": decay,
                "rerank": {key: value for key, value in rerank.items() if key != "ordered"}
                | {
                    "cross_scores": {
                        row["item_id"]: row["cross_score"] for row in rerank.get("ordered", [])
                    }
                },
                "not_current": [
                    {key: value for key, value in fact.items() if key != "fact"}
                    for fact in result.not_current
                ],
                "withheld": withheld,
            },
        }
        case_id, error = await self._record_case(tier, principal, query, combiner, kept, stages)
        answer = {
            **base,
            "status": "ok" if len(used) == len(combiner.models) else "partial",
            "hits": events[:limit],
            "combiner": combiner.describe(),
            "models": models,
            "decay": decay,
            "words": words_report,
            "merged_order": [item for item, _ in first_stage],
            "rerank": rerank,
            "not_current": result.not_current,
            "rankings": {
                label: [
                    {
                        key: hit[key]
                        for key in ("item_id", "event_id", "distance", "text", "memory", "content")
                        if key in hit
                    }
                    for hit in kept[label][:limit]
                ]
                for label in used
            },
            "ground_truth_case": case_id,
        }
        if error is not None:
            answer["ground_truth_error"] = error
        return answer

    async def _words_ranking(
        self, query: str, kept: dict[str, list[dict[str, Any]]], used: list[str]
    ) -> tuple[list[str] | None, dict[str, Any]]:
        """Ask the supplier which of the models' candidate hits share words with the question.

        The candidates are the hits the used models ranked (their item ids and text); the supplier's
        keyword capability ranks them and memory scores nothing. Returns (item ids best first, report);
        the ranking is None, with the reason in the report, when the supplier cannot answer or no
        candidate has text. A failed words ranking never fails the search: the models' own merge stands.
        """
        settings = self.settings
        if settings is None or settings.words_capability is None or self.numbers is None:
            raise RuntimeError(
                "A words ranking needs the declared words_capability and the supplier"
            )
        candidates: dict[str, str] = {}
        for label in used:
            for hit in kept[label]:
                text = hit.get("text") or hit.get("memory") or hit.get("content")
                if isinstance(text, str) and text.strip():
                    candidates.setdefault(hit["item_id"], text)
        if not candidates:
            return None, {"applied": False, "reason": "no candidate hit carries text"}
        capability = settings.words_capability
        try:
            ranked = await self.numbers.word_ranking(
                capability, query, [{"id": item, "text": text} for item, text in candidates.items()]
            )
        except NumbersNotYetSupplied as pending:
            return None, {"applied": False, "capability": capability, "reason": str(pending)}
        return ranked, {
            "applied": True,
            "capability": capability,
            "ranked": len(ranked),
            "candidates": len(candidates),
            "order": ranked,
        }

    async def _combine(
        self,
        combiner: CombinerRow,
        kept: dict[str, list[dict[str, Any]]],
        used: list[str],
        words: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return (item id, score) best first from the combiner's rule over the used rankings.

        Reciprocal rank fusion is computed here, with the supplier's keyword ranking added as one more
        ranking when there is one. A trained artifact is asked through the supplied object, never run
        by memory; its order is kept and its scores are the rank positions' reciprocals only for
        reporting. Raises RuntimeError for an artifact combiner whose artifact was not supplied.
        """
        rankings = {label: [h["item_id"] for h in kept[label]] for label in used}
        if combiner.rule == RULE_RRF:
            if self.settings is None:
                raise RuntimeError("Merging rankings needs the declared search settings")
            weights = None
            if self.settings.model_weights is not None:
                weights = {label: self.settings.model_weights[label] for label in used}
            if words:
                rankings[f"words:{self.settings.words_capability}"] = words
                if weights is not None:
                    if self.settings.words_weight is None:
                        raise RuntimeError("model_weights are declared without a words_weight")
                    weights[f"words:{self.settings.words_capability}"] = self.settings.words_weight
            return reciprocal_rank_fusion(rankings, self.settings.merging_constant, weights)
        if combiner.rule == RULE_ARTIFACT:
            artifact = self.artifacts.get(combiner.combiner_id)
            if artifact is None:
                raise RuntimeError(
                    f"The artifact for combiner {combiner.combiner_id} is not supplied"
                )
            order = (await artifact.merge([{"case_id": None, "rankings": rankings}]))[None]
            return [(item, 1.0 / rank) for rank, item in enumerate(order, start=1)]
        raise RuntimeError(f"Unknown combiner rule {combiner.rule!r}")

    async def _rerank(
        self,
        query: str,
        merged: list[tuple[str, float]],
        kept: dict[str, list[dict[str, Any]]],
        used: list[str],
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """Second stage: the supplier's cross-scorer reads the question beside each best result.

        The first rerank_candidates results of the merged order that carry text are each paired with
        the question and scored by the supplier's cross-scorer. The merged order and the
        cross-scorer's order are then fused with the declared weights (the cross-scorer has
        rerank_weight, the merged order the rest). Results below those candidates keep their order
        and first-stage scores after them; the scores of the candidates are the second fusion's.
        When the supplier cannot score, the merged order stands and the report says why. Returns
        (order, report); the report lists the candidates in final order with their cross-scores.
        """
        settings = self.settings
        if (
            settings is None
            or settings.rerank_capability is None
            or settings.rerank_candidates is None
            or settings.rerank_weight is None
            or self.numbers is None
        ):
            raise RuntimeError("A second stage needs the declared rerank settings and the supplier")
        capability = settings.rerank_capability
        text_of: dict[str, str] = {}
        for label in used:
            for hit in kept[label]:
                text = hit.get("text") or hit.get("memory") or hit.get("content")
                if isinstance(text, str) and text.strip():
                    text_of.setdefault(hit["item_id"], text)
        head = [item for item, _ in merged[: settings.rerank_candidates] if item in text_of]
        if len(head) < 2:
            return merged, {
                "applied": False,
                "capability": capability,
                "reason": "fewer than two of the best results carry text",
            }
        try:
            scores = await self.numbers.pair_scores(
                capability, query, [{"id": item, "text": text_of[item]} for item in head]
            )
        except NumbersNotYetSupplied as pending:
            return merged, {"applied": False, "capability": capability, "reason": str(pending)}
        by_score = sorted(head, key=lambda item: (-scores[item], head.index(item)))
        fused = reciprocal_rank_fusion(
            {"merged": head, "cross_scorer": by_score},
            settings.merging_constant,
            {"merged": 1.0 - settings.rerank_weight, "cross_scorer": settings.rerank_weight},
        )
        covered = set(head)
        order = fused + [pair for pair in merged if pair[0] not in covered]
        return order, {
            "applied": True,
            "capability": capability,
            "candidates": len(head),
            "weight": settings.rerank_weight,
            "cross_scorer_order": by_score,
            "ordered": [
                {"item_id": item, "cross_score": scores[item], "text": text_of[item][:80]}
                for item, _ in fused
            ],
        }

    async def _apply_decay(
        self,
        merged: list[tuple[str, float]],
        kept: dict[str, list[dict[str, Any]]],
        used: list[str],
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """Bias each merged score by the age of its event and re-order; never remove an item.

        Used only when the deployment declared decay_time_constant_seconds. Each item's
        score is multiplied by the bounded exponential factor of its event's age and kept
        within [0, 1]; items keep their combiner order among equal scores. Returns the
        biased list and a report naming the time constant. Raises ValueError for an event
        whose created_at is not an ISO 8601 time, because its age cannot be known.
        """
        settings = self.settings
        if settings is None or settings.decay_time_constant_seconds is None:
            raise RuntimeError("Decay needs a declared decay_time_constant_seconds")
        tau = settings.decay_time_constant_seconds
        lower, upper = settings.decay_lower_bound, settings.decay_upper_bound
        if lower is None or upper is None:
            raise RuntimeError("Decay needs declared decay_lower_bound and decay_upper_bound")
        event_of = {hit["item_id"]: hit["event_id"] for label in used for hit in kept[label]}

        def read() -> dict[str, str]:
            """Read each involved event's recorded creation time on the owner's worker."""
            ids = sorted(set(event_of.values()))
            rows = self.conn.native.execute(
                "SELECT event_id, created_at FROM memory_events WHERE event_id IN (SELECT unnest(?))",
                [ids],
            ).fetchall()
            return dict(rows)

        created = await self.conn.run(read)
        now = datetime.now(timezone.utc)
        assembler = ContextAssembler(lower, upper)
        biased: list[tuple[str, float]] = []
        for item_id, score in merged:
            stamp = datetime.fromisoformat(created[event_of[item_id]])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = (now - stamp).total_seconds()
            biased.append((item_id, assembler.calculate_decay_score(score, age, tau)))
        ordered = sorted(enumerate(biased), key=lambda pair: (-pair[1][1], pair[0]))
        return [pair[1] for pair in ordered], {"applied": True, "time_constant_seconds": tau}

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

    async def _record_case(
        self,
        tier: str,
        principal: AuthenticatedPrincipal,
        query: str,
        combiner: CombinerRow,
        kept: dict[str, list[dict[str, Any]]],
        stages: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Record a ground-truth case when the models disagree or the question has a known answer.

        stages holds the run's other orders and reports: "merged" and "final" (item, score) lists,
        "words" (item ids or None), "rerank" (the second stage's report) and "report" (what else the
        search measured). The case keeps every reader's ranking, the keyword ranking, the merged
        order and the order after the second stage, the settings in force and the reports, so that
        whether each was right can be read against the known answer, now or after a correction.
        The case id is derived from the question, every order and the combiner, so asking the same
        thing twice records one case. The case points to the events involved and copies none of
        their text. A failure to record is returned, never raised or hidden, so the search itself
        still answers. Returns (case id, error); (None, None) when nothing needed recording.
        """

        settings = self.settings
        if settings is None:
            raise RuntimeError("Recording a case needs the declared search settings")

        def output(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Shape a ranking for the ground-truth record: ids, kind and native scores, no text."""
            return [
                {
                    "item_id": h["item_id"],
                    "event_id": h["event_id"],
                    "item_kind": item_kind(h),
                    "score": h.get("distance", h.get("score")),
                }
                for h in hits
            ]

        outputs = [
            {"key": label, "role": MODEL_OUTPUT, "output": output(hits)}
            for label, hits in sorted(kept.items())
        ]
        known = await self.conn.run(lambda: latest_known(self.conn.native, query))
        if known is None and not disagree(outputs):
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
                    [item for item, _ in stages["merged"]],
                    [item for item, _ in stages["final"]],
                    stages["words"],
                ],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        case_id = f"case-{digest}"
        now = datetime.now(timezone.utc).isoformat()
        by_item = {h["item_id"]: h for hits in kept.values() for h in hits}

        def scored(
            pairs: list[tuple[str, float | None]], cross: dict[str, float] | None = None
        ) -> list[dict[str, Any]]:
            """Shape an (item, score) order for the record, with the cross-scorer's score when given."""
            return [
                {
                    "item_id": item,
                    "event_id": by_item[item]["event_id"],
                    "item_kind": item_kind(by_item[item]),
                    "score": score,
                    **({} if cross is None else {"cross_score": cross.get(item)}),
                }
                for item, score in pairs
            ]

        extra: list[tuple[str, list[dict[str, Any]]]] = [
            (COMBINED_OUTPUT, scored(stages["merged"]))
        ]
        if stages["words"]:
            extra.append((WORDS_OUTPUT, scored([(item, None) for item in stages["words"]])))
        if stages["rerank"].get("applied"):
            cross = {row["item_id"]: row["cross_score"] for row in stages["rerank"]["ordered"]}
            extra.append((RERANKED_OUTPUT, scored(stages["final"], cross)))

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
                for role, ranking in extra:
                    record_output(
                        handle,
                        case_id=case_id,
                        role=role,
                        model_id=None,
                        revision=None,
                        output=ranking,
                        combiner_id=combiner.combiner_id,
                        recorded_at=now,
                    )
                record_run(
                    handle,
                    case_id=case_id,
                    query=query,
                    tier=tier,
                    ran_at=now,
                    settings=settings.report(),
                    report=stages["report"],
                )
                handle.commit()
            except BaseException:
                handle.rollback()
                raise

        try:
            await self.conn.run(write)
        except ValueError as error:
            logger.exception("Recording the ground-truth case failed")
            return None, f"{type(error).__name__}: {error}"
        return case_id, None
