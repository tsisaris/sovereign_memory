"""The combiner registry, reciprocal rank fusion and the held-back comparison.

A combiner is the rule that merges several models' rankings into one answer.
Every combiner ever proposed is a row (id, rule, models used, settings read,
proposer, time); every decision about which one is current is an appended row,
and the current combiner is the latest adopted one, so exactly one is current.
The starting combiner is reciprocal rank fusion (Cormack, Clarke and Buettcher,
SIGIR 2009): an item scores the sum over models of 1 divided by the merging
constant plus its rank. A candidate replaces the current combiner only when it
scores strictly better on held-back, judged cases and enough such cases exist;
otherwise the current one stays and the reason is recorded. A trained combiner
is an artifact supplied by agent_uagent_builder: memory never runs it, it hands
the builder the held-back inputs without judgments and scores the merged
rankings that come back against the judgments itself. This module never trains
a model and never runs one.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

import duckdb

from service.memory_ground_truth import scorable_held_back
from service.memory_models import list_models
from service.memory_settings import SearchSettings

RULE_RRF = "reciprocal_rank_fusion"
RULE_ARTIFACT = "external_artifact"
RULES = (RULE_RRF, RULE_ARTIFACT)
STARTING_COMBINER_ID = "rrf-start"
STARTING_PROPOSER = "owner-system"
DECISION_SEEDED = "seeded"
DECISION_ADOPTED = "adopted"
DECISION_KEPT = "kept_current"

COMBINER_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_combiners (
    combiner_id VARCHAR PRIMARY KEY,
    rule VARCHAR NOT NULL,
    models VARCHAR NOT NULL,
    settings_read VARCHAR NOT NULL,
    proposed_by VARCHAR NOT NULL,
    proposed_at VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_combiner_decisions (
    decision_id VARCHAR NOT NULL,
    combiner_id VARCHAR NOT NULL,
    replaces VARCHAR,
    decision VARCHAR NOT NULL,
    comparison_score VARCHAR,
    candidate_score DOUBLE,
    current_score DOUBLE,
    held_back_cases INTEGER,
    smallest_held_back_set INTEGER,
    settings_used VARCHAR,
    reason VARCHAR NOT NULL,
    decided_at VARCHAR NOT NULL,
    FOREIGN KEY (combiner_id) REFERENCES memory_combiners(combiner_id)
);
"""


class ArtifactCombiner(Protocol):
    """A trained combiner supplied by agent_uagent_builder; memory never runs it itself.

    Memory hands merge the held-back inputs without judgments and gets back one
    merged ranking of item ids per case id. Memory then scores those rankings.
    """

    async def merge(self, inputs: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Return case id -> merged ranking of item ids, for each input case."""
        ...


@dataclass(frozen=True)
class CombinerRow:
    """One registered combiner, exactly as proposed."""

    combiner_id: str
    rule: str
    models: tuple[str, ...]
    settings_read: tuple[str, ...]
    proposed_by: str
    proposed_at: str

    def describe(self) -> dict[str, Any]:
        """Return the combiner as a plain dict, for reporting with an answer."""
        return {
            "id": self.combiner_id,
            "rule": self.rule,
            "models": list(self.models),
            "settings_read": list(self.settings_read),
        }


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]], merging_constant: float
) -> list[tuple[str, float]]:
    """Merge rankings by reciprocal rank fusion; pure.

    Goes in: model label -> item ids best first, and the merging constant. Comes
    out: (item id, score) best first, where an item's score is the sum over
    models of 1 / (merging_constant + rank) with rank counting from 1 and an
    item absent from a model adding nothing for it. Ties are broken by the best
    rank any model gave the item, then by item id, so the order is repeatable.
    """
    scores: dict[str, float] = {}
    best: dict[str, int] = {}
    for items in rankings.values():
        for rank, item in enumerate(items, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (merging_constant + rank)
            best[item] = min(best.get(item, rank), rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], best[pair[0]], pair[0]))


def mean_reciprocal_rank(rankings: list[tuple[list[str], set[str]]]) -> float:
    """Return the mean over cases of 1 / rank of the first correct item (0 when none is ranked); pure."""
    total = 0.0
    for ranking, correct in rankings:
        for rank, item in enumerate(ranking, start=1):
            if item in correct:
                total += 1.0 / rank
                break
    return total / len(rankings) if rankings else 0.0


def top_one_accuracy(rankings: list[tuple[list[str], set[str]]]) -> float:
    """Return the share of cases whose first-ranked item is correct; pure."""
    hits = sum(1 for ranking, correct in rankings if ranking and ranking[0] in correct)
    return hits / len(rankings) if rankings else 0.0


SCORES = {"mean_reciprocal_rank": mean_reciprocal_rank, "top_one_accuracy": top_one_accuracy}


def initialize_combiner_schema(handle: duckdb.DuckDBPyConnection) -> None:
    """Create the combiner tables if absent, inside the caller's transaction."""
    handle.execute(COMBINER_SCHEMA)


def _row(values: tuple) -> CombinerRow:
    """Build a CombinerRow from a memory_combiners row in column order."""
    combiner_id, rule, models, settings_read, proposed_by, proposed_at = values
    return CombinerRow(
        combiner_id,
        rule,
        tuple(json.loads(models)),
        tuple(json.loads(settings_read)),
        proposed_by,
        proposed_at,
    )


def get_combiner(handle: duckdb.DuckDBPyConnection, combiner_id: str) -> CombinerRow | None:
    """Return one registered combiner or None. Reads only."""
    row = handle.execute(
        "SELECT combiner_id, rule, models, settings_read, proposed_by, proposed_at "
        "FROM memory_combiners WHERE combiner_id = ?",
        [combiner_id],
    ).fetchone()
    return None if row is None else _row(row)


def register_combiner(
    handle: duckdb.DuckDBPyConnection,
    *,
    combiner_id: str,
    rule: str,
    models: list[str],
    settings_read: list[str],
    proposed_by: str,
    proposed_at: str,
) -> CombinerRow:
    """Propose a combiner; it does not become current. Raises ValueError for an unknown rule or a reused id.

    Proposing the identical combiner again returns the stored row. A combiner
    can only name models that are registered.
    """
    if rule not in RULES:
        raise ValueError(f"Unknown combiner rule {rule!r}")
    if not combiner_id or not models:
        raise ValueError("A combiner needs an id and at least one model")
    known = {m.label for m in list_models(handle)}
    unknown = sorted(set(models) - known)
    if unknown:
        raise ValueError(f"Combiner names unregistered models: {unknown}")
    stored = get_combiner(handle, combiner_id)
    wanted = (rule, tuple(sorted(models)), tuple(sorted(settings_read)))
    if stored is not None:
        if (
            stored.rule,
            tuple(sorted(stored.models)),
            tuple(sorted(stored.settings_read)),
        ) != wanted:
            raise ValueError(f"Combiner {combiner_id} already exists with different content")
        return stored
    handle.execute(
        "INSERT INTO memory_combiners VALUES (?, ?, ?, ?, ?, ?)",
        [
            combiner_id,
            rule,
            json.dumps(sorted(models)),
            json.dumps(sorted(settings_read)),
            proposed_by,
            proposed_at,
        ],
    )
    return get_combiner(handle, combiner_id)


def current_combiner(handle: duckdb.DuckDBPyConnection) -> CombinerRow | None:
    """Return the one current combiner (the latest seeded or adopted), or None before any exists. Reads only."""
    row = handle.execute(
        "SELECT combiner_id FROM memory_combiner_decisions WHERE decision IN (?, ?) "
        "ORDER BY CAST(decided_at AS TIMESTAMPTZ) DESC, rowid DESC LIMIT 1",
        [DECISION_SEEDED, DECISION_ADOPTED],
    ).fetchone()
    return None if row is None else get_combiner(handle, row[0])


def ensure_starting_combiner(
    handle: duckdb.DuckDBPyConnection, decided_at: str
) -> CombinerRow | None:
    """Seed reciprocal rank fusion over the registered embedding models when no combiner exists yet.

    Returns the current combiner, or None while no embedding model is registered
    (there is nothing to merge). Models registered later join no combiner until a
    candidate that names them beats the current one on held-back cases.
    """
    existing = current_combiner(handle)
    if existing is not None:
        return existing
    labels = [m.label for m in list_models(handle, "embedding")]
    if not labels:
        return None
    row = register_combiner(
        handle,
        combiner_id=STARTING_COMBINER_ID,
        rule=RULE_RRF,
        models=labels,
        settings_read=["merging_constant"],
        proposed_by=STARTING_PROPOSER,
        proposed_at=decided_at,
    )
    handle.execute(
        "INSERT INTO memory_combiner_decisions VALUES (?, ?, NULL, ?, NULL, NULL, NULL, NULL, "
        "NULL, NULL, ?, ?)",
        [
            f"decision-{STARTING_COMBINER_ID}",
            row.combiner_id,
            DECISION_SEEDED,
            "starting combiner: reciprocal rank fusion, until a better one is accepted",
            decided_at,
        ],
    )
    return row


def _merged(
    combiner: CombinerRow, rankings: dict[str, list[str]], settings: SearchSettings
) -> list[str] | None:
    """Merge a case's rankings with a rank-fusion combiner; None when a model it uses has no ranking."""
    if combiner.rule != RULE_RRF or any(label not in rankings for label in combiner.models):
        return None
    chosen = {label: rankings[label] for label in combiner.models}
    return [item for item, _ in reciprocal_rank_fusion(chosen, settings.merging_constant)]


def prepare_comparison(
    handle: duckdb.DuckDBPyConnection, candidate_id: str, memory_kind: str | None = None
) -> tuple[CombinerRow, CombinerRow, list[dict[str, Any]]]:
    """Gather what a comparison needs: the candidate, the current combiner and the shared cases.

    The cases are held-back cases whose source was not removed and whose latest
    judgment names the correct items, restricted to those every model of both
    combiners has an output for, so both are scored on the very same cases.
    Raises ValueError for an unknown candidate, when no combiner is current, or
    when the candidate is the current combiner. Reads only.
    """
    candidate = get_combiner(handle, candidate_id)
    current = current_combiner(handle)
    if candidate is None or current is None:
        raise ValueError("Both the candidate and a current combiner must exist")
    if candidate.combiner_id == current.combiner_id:
        raise ValueError("A combiner cannot be compared with itself")
    needed = set(candidate.models) | set(current.models)
    cases = [
        case for case in scorable_held_back(handle, memory_kind) if needed <= set(case["rankings"])
    ]
    return candidate, current, cases


async def judge_comparison(
    candidate: CombinerRow,
    current: CombinerRow,
    cases: list[dict[str, Any]],
    settings: SearchSettings,
    artifacts: dict[str, ArtifactCombiner],
) -> dict[str, Any]:
    """Score both combiners on the same cases and decide; touches no database.

    The candidate replaces the current combiner only when at least
    smallest_held_back_set cases exist and its score, by the declared comparison
    score, is strictly higher; otherwise the current stays, with the reason. A
    rank-fusion combiner is computed here from the stored model rankings; an
    artifact combiner is asked through its supplied object, given the inputs
    without judgments, and the rankings it returns are scored here. Comes out:
    {"decision", "candidate_score", "current_score", "held_back_cases", "reason"}.
    """
    score = SCORES[settings.comparison_score]
    results: dict[str, list[tuple[list[str], set[str]]]] = {}
    for combiner in (current, candidate):
        merged: dict[str, list[str]] = {}
        if combiner.rule == RULE_ARTIFACT:
            artifact = artifacts.get(combiner.combiner_id)
            if artifact is None:
                return {
                    "decision": DECISION_KEPT,
                    "candidate_score": None,
                    "current_score": None,
                    "held_back_cases": len(cases),
                    "reason": f"artifact for {combiner.combiner_id} is not supplied; current stays",
                }
            merged = await artifact.merge(
                [
                    {
                        "case_id": c["case_id"],
                        "memory_kind": c["memory_kind"],
                        "input": c["input"],
                        "rankings": {k: c["rankings"][k] for k in combiner.models},
                    }
                    for c in cases
                ]
            )
        else:
            for case in cases:
                ranking = _merged(combiner, case["rankings"], settings)
                if ranking is not None:
                    merged[case["case_id"]] = ranking
        results[combiner.combiner_id] = [
            (merged.get(case["case_id"], []), case["correct"]) for case in cases
        ]
    current_score = score(results[current.combiner_id])
    candidate_score = score(results[candidate.combiner_id])
    if len(cases) < settings.smallest_held_back_set:
        decision = DECISION_KEPT
        reason = (
            f"too few held-back judged cases: {len(cases)} found, "
            f"{settings.smallest_held_back_set} needed; current stays"
        )
    elif candidate_score > current_score:
        decision = DECISION_ADOPTED
        reason = (
            f"{settings.comparison_score} {candidate_score:.6f} beats "
            f"{current_score:.6f} on {len(cases)} held-back cases"
        )
    else:
        decision = DECISION_KEPT
        reason = (
            f"{settings.comparison_score} {candidate_score:.6f} does not beat "
            f"{current_score:.6f} on {len(cases)} held-back cases; current stays"
        )
    return {
        "decision": decision,
        "candidate_score": candidate_score,
        "current_score": current_score,
        "held_back_cases": len(cases),
        "reason": reason,
    }


def record_decision(
    handle: duckdb.DuckDBPyConnection,
    candidate: CombinerRow,
    current: CombinerRow,
    outcome: dict[str, Any],
    settings: SearchSettings,
    decided_at: str,
) -> dict[str, Any]:
    """Append the decision row for a comparison and return the outcome with its context.

    The only place a decision is written. Earlier decisions are never edited. The
    settings in effect are recorded with the decision so it reports the value used.
    """
    decision_id = f"decision-{candidate.combiner_id}-{decided_at}"
    handle.execute(
        "INSERT INTO memory_combiner_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            decision_id,
            candidate.combiner_id,
            current.combiner_id,
            outcome["decision"],
            settings.comparison_score,
            outcome["candidate_score"],
            outcome["current_score"],
            outcome["held_back_cases"],
            settings.smallest_held_back_set,
            json.dumps(settings.report()),
            outcome["reason"],
            decided_at,
        ],
    )
    return {
        **outcome,
        "candidate": candidate.combiner_id,
        "current_before": current.combiner_id,
        "settings_used": settings.report(),
    }
