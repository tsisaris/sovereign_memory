"""Ground truth: append-only cases, every model's output for them, and the judgments.

A case is a thing to judge: a retrieved item right or wrong, a choice where
models disagreed, or a classification. It points to events and never copies
their text. Each case carries its memory kind and is fixed once, from its id,
as training or held-back. Model outputs record what each model that took part
answered, with the combiner current at the time. A judgment says who judged,
when, the verdict, the chosen items or typed answer and a note; a later judgment
on a case is a new row that supersedes the earlier one. Nothing here is ever
updated or deleted. Forgetting hides an item: a case whose event was removed is
reported as source-removed and leaves training and held-back scoring, while its
pointer and judgment rows stay. An export manifest (case ids and a content
fingerprint) lets a training run be repeated exactly. This module never trains,
never runs a model and never copies event text.
"""

import hashlib
import json
import uuid
from typing import Any

import duckdb

from service.memory_models import get_model, model_label

CASE_KINDS = ("retrieved_item", "model_choice", "classification")
INPUT_KINDS = ("question", "event")
SPLIT_TRAINING = "training"
SPLIT_HELD_BACK = "held_back"
VERDICTS = ("right", "wrong", "pick", "typed", "none_correct")
# Verdicts that name the correct item or items, which a combiner can be scored against.
SCORABLE_VERDICTS = ("right", "pick")
COMBINED_OUTPUT = "combined"
MODEL_OUTPUT = "model"
WORDS_OUTPUT = "words"
RERANKED_OUTPUT = "reranked"
# Outputs that belong to no single registered model; each is stored under its own role name.
UNMODELLED_OUTPUTS = (COMBINED_OUTPUT, WORDS_OUTPUT, RERANKED_OUTPUT)
EXPORT_PURPOSES = ("training", "held_back_inputs")

GROUND_TRUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_gt_cases (
    case_id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    memory_kind VARCHAR NOT NULL,
    input_kind VARCHAR NOT NULL,
    input VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    created_by VARCHAR NOT NULL,
    split VARCHAR NOT NULL,
    held_back_share DOUBLE NOT NULL,
    selected_by_owner BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_gt_case_events (
    case_id VARCHAR NOT NULL,
    event_id VARCHAR NOT NULL,
    PRIMARY KEY (case_id, event_id),
    FOREIGN KEY (case_id) REFERENCES memory_gt_cases(case_id),
    FOREIGN KEY (event_id) REFERENCES memory_events(event_id)
);
CREATE TABLE IF NOT EXISTS memory_gt_outputs (
    case_id VARCHAR NOT NULL,
    output_key VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    model_id VARCHAR,
    revision VARCHAR,
    width INTEGER,
    output VARCHAR NOT NULL,
    combiner_id VARCHAR NOT NULL,
    recorded_at VARCHAR NOT NULL,
    PRIMARY KEY (case_id, output_key),
    FOREIGN KEY (case_id) REFERENCES memory_gt_cases(case_id)
);
CREATE TABLE IF NOT EXISTS memory_gt_judgments (
    judgment_id VARCHAR PRIMARY KEY,
    case_id VARCHAR NOT NULL,
    judged_by VARCHAR NOT NULL,
    judged_at VARCHAR NOT NULL,
    verdict VARCHAR NOT NULL,
    chosen_items VARCHAR NOT NULL,
    typed_answer VARCHAR,
    note VARCHAR,
    supersedes VARCHAR,
    FOREIGN KEY (case_id) REFERENCES memory_gt_cases(case_id)
);
CREATE TABLE IF NOT EXISTS memory_gt_exports (
    export_id VARCHAR PRIMARY KEY,
    purpose VARCHAR NOT NULL,
    case_ids VARCHAR NOT NULL,
    fingerprint VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    created_by VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_gt_known (
    known_id VARCHAR PRIMARY KEY,
    query VARCHAR NOT NULL,
    answer VARCHAR NOT NULL,
    right_events VARCHAR NOT NULL,
    basis VARCHAR NOT NULL,
    said_by VARCHAR NOT NULL,
    recorded_by VARCHAR NOT NULL,
    recorded_at VARCHAR NOT NULL,
    supersedes VARCHAR
);
CREATE TABLE IF NOT EXISTS memory_gt_runs (
    case_id VARCHAR PRIMARY KEY,
    query VARCHAR NOT NULL,
    tier VARCHAR NOT NULL,
    ran_at VARCHAR NOT NULL,
    settings VARCHAR NOT NULL,
    report VARCHAR NOT NULL,
    FOREIGN KEY (case_id) REFERENCES memory_gt_cases(case_id)
);
CREATE OR REPLACE VIEW memory_gt_current_known AS
SELECT k.* FROM memory_gt_known k
WHERE NOT EXISTS (SELECT 1 FROM memory_gt_known n WHERE n.supersedes = k.known_id);
CREATE OR REPLACE VIEW memory_gt_ranking_items AS
SELECT o.case_id, o.output_key, o.role, o.model_id, o.revision,
       CAST(j.key AS INTEGER) + 1 AS rank,
       json_extract_string(j.value, '$.item_id') AS item_id,
       json_extract_string(j.value, '$.event_id') AS event_id,
       json_extract_string(j.value, '$.item_kind') AS item_kind,
       TRY_CAST(json_extract_string(j.value, '$.score') AS DOUBLE) AS score,
       TRY_CAST(json_extract_string(j.value, '$.cross_score') AS DOUBLE) AS cross_score
FROM memory_gt_outputs o, json_each(o.output) j
WHERE json_type(o.output) = 'ARRAY';
CREATE OR REPLACE VIEW memory_gt_ranking_scores AS
SELECT i.case_id, c.input AS query, c.memory_kind AS tier, i.output_key, i.role,
       k.known_id, k.recorded_by AS truth_by,
       min(i.rank) FILTER (WHERE list_contains(from_json(k.right_events, '["VARCHAR"]'), i.event_id))
           AS first_right_rank,
       coalesce(min(i.rank) FILTER (
           WHERE list_contains(from_json(k.right_events, '["VARCHAR"]'), i.event_id)) = 1, false)
           AS right_at_1,
       coalesce(1.0 / min(i.rank) FILTER (
           WHERE list_contains(from_json(k.right_events, '["VARCHAR"]'), i.event_id)), 0.0)
           AS reciprocal_rank
FROM memory_gt_ranking_items i
JOIN memory_gt_cases c ON c.case_id = i.case_id
JOIN memory_gt_current_known k ON k.query = c.input
GROUP BY i.case_id, c.input, c.memory_kind, i.output_key, i.role, k.known_id, k.recorded_by;
"""


def initialize_ground_truth_schema(handle: duckdb.DuckDBPyConnection) -> None:
    """Create the ground-truth tables if absent, inside the caller's transaction."""
    handle.execute(GROUND_TRUTH_SCHEMA)


def split_of(case_id: str, held_back_share: float) -> str:
    """Return "held_back" or "training" for a case id and a share; pure and repeatable.

    The id is hashed to a number in [0, 1); the case is held back when that number
    falls below the share. The same id and share always give the same split.
    """
    bucket = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16) / 16**8
    return SPLIT_HELD_BACK if bucket < held_back_share else SPLIT_TRAINING


def record_case(
    handle: duckdb.DuckDBPyConnection,
    *,
    case_id: str,
    kind: str,
    memory_kind: str,
    input_kind: str,
    input_value: str,
    events: list[str],
    created_at: str,
    created_by: str,
    held_back_share: float,
    selected_by_owner: bool = False,
) -> dict[str, Any]:
    """Record one case, once, and return it; recording the identical case again returns the stored one.

    input_kind "question" keeps the search question text as input_value; input_kind
    "event" keeps only an event id (a pointer, never the event's text) that must be
    one of the case's events. The split is fixed here from the id and the share and
    never changes. Raises ValueError for an unknown kind, an empty id or event
    list, an unknown event, or an id already used by a different case. Nothing is
    updated.
    """
    if kind not in CASE_KINDS or input_kind not in INPUT_KINDS:
        raise ValueError("Unknown case kind or input kind")
    if not isinstance(case_id, str) or not case_id or not events:
        raise ValueError("A case needs an id and at least one event")
    if input_kind == "event" and input_value not in events:
        raise ValueError("An event input must be a pointer to one of the case's events")
    unique = sorted(set(events))
    for event_id in unique:
        if (
            handle.execute("SELECT 1 FROM memory_events WHERE event_id = ?", [event_id]).fetchone()
            is None
        ):
            raise ValueError(f"Case names unknown event {event_id}")
    stored = handle.execute(
        "SELECT kind, memory_kind, input_kind, input FROM memory_gt_cases WHERE case_id = ?",
        [case_id],
    ).fetchone()
    if stored is not None:
        linked = [
            r[0]
            for r in handle.execute(
                "SELECT event_id FROM memory_gt_case_events WHERE case_id = ? ORDER BY event_id",
                [case_id],
            ).fetchall()
        ]
        if stored != (kind, memory_kind, input_kind, input_value) or linked != unique:
            raise ValueError(f"Case {case_id} already exists with different content")
        return case_row(handle, case_id)
    handle.execute(
        "INSERT INTO memory_gt_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            case_id,
            kind,
            memory_kind,
            input_kind,
            input_value,
            created_at,
            created_by,
            split_of(case_id, held_back_share),
            held_back_share,
            selected_by_owner,
        ],
    )
    for event_id in unique:
        handle.execute("INSERT INTO memory_gt_case_events VALUES (?, ?)", [case_id, event_id])
    return case_row(handle, case_id)


def case_row(handle: duckdb.DuckDBPyConnection, case_id: str) -> dict[str, Any]:
    """Return one case with its events and whether its source was removed; raises ValueError if unknown.

    source_removed is derived from the owner's removal records at the moment of
    reading; it needs no rewrite of the case. Reads only.
    """
    row = handle.execute(
        "SELECT case_id, kind, memory_kind, input_kind, input, created_at, created_by, split, "
        "held_back_share, selected_by_owner FROM memory_gt_cases WHERE case_id = ?",
        [case_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown case {case_id}")
    keys = (
        "case_id",
        "kind",
        "memory_kind",
        "input_kind",
        "input",
        "created_at",
        "created_by",
        "split",
        "held_back_share",
        "selected_by_owner",
    )
    case = dict(zip(keys, row, strict=True))
    events = [
        r[0]
        for r in handle.execute(
            "SELECT event_id FROM memory_gt_case_events WHERE case_id = ? ORDER BY event_id",
            [case_id],
        ).fetchall()
    ]
    removed = handle.execute(
        "SELECT count(*) FROM memory_gt_case_events c JOIN memory_removals r "
        "ON r.target_event_id = c.event_id WHERE c.case_id = ?",
        [case_id],
    ).fetchone()[0]
    return {**case, "events": events, "source_removed": removed > 0}


def record_output(
    handle: duckdb.DuckDBPyConnection,
    *,
    case_id: str,
    role: str,
    model_id: str | None,
    revision: str | None,
    output: Any,
    combiner_id: str,
    recorded_at: str,
) -> None:
    """Record what one model (or the combined answer) answered for a case; never overwrites.

    A "model" output carries the registered model id and revision; its width is
    read from the registry. The "combined" (merged), "words" (keyword) and "reranked" (after the
    second stage) outputs have none. output is a JSON-able
    ranking (a list of {"item_id", "event_id", "score"}) or class scores. Recording
    the same output again is a no-op; a different output for the same participant
    raises ValueError, because nothing is overwritten.
    """
    if role == MODEL_OUTPUT:
        model = get_model(handle, model_id or "", revision or "")
        if model is None:
            raise ValueError("A model output must name a registered model revision")
        key, width = model_label(model.model_id, model.revision), model.width
    elif role in UNMODELLED_OUTPUTS:
        key, width, model_id, revision = role, None, None, None
    else:
        raise ValueError(f"Unknown output role {role!r}")
    if (
        handle.execute("SELECT 1 FROM memory_gt_cases WHERE case_id = ?", [case_id]).fetchone()
        is None
    ):
        raise ValueError(f"Unknown case {case_id}")
    encoded = json.dumps(output, sort_keys=True)
    stored = handle.execute(
        "SELECT output FROM memory_gt_outputs WHERE case_id = ? AND output_key = ?",
        [case_id, key],
    ).fetchone()
    if stored is not None:
        if stored[0] != encoded:
            raise ValueError(f"Case {case_id} already has a different output for {key}")
        return
    handle.execute(
        "INSERT INTO memory_gt_outputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [case_id, key, role, model_id, revision, width, encoded, combiner_id, recorded_at],
    )


def record_known(
    handle: duckdb.DuckDBPyConnection,
    *,
    query: str,
    answer: str,
    right_events: list[str],
    basis: str,
    said_by: str,
    recorded_by: str,
    recorded_at: str,
) -> str:
    """Append the known-right answer to a test question and return its id; a later one supersedes.

    Goes in: the question, the answer in words, the events whose statements give that answer (an item
    is right when it comes from one of them), the basis ("construction" for a set built to have one
    answer, or who corrected it), who said so, who recorded it and when. Nothing earlier is edited:
    the new row points at the row it supersedes, and scoring reads only the row nothing supersedes.
    Raises ValueError for an empty question, answer, basis or event list, or an unknown event.
    """
    if not query or not answer or not basis or not said_by or not right_events:
        raise ValueError(
            "A known answer needs a question, an answer, a basis, who said it and events"
        )
    unique = sorted(set(right_events))
    for event_id in unique:
        if (
            handle.execute("SELECT 1 FROM memory_events WHERE event_id = ?", [event_id]).fetchone()
            is None
        ):
            raise ValueError(f"Known answer names unknown event {event_id}")
    previous = latest_known(handle, query)
    known_id = str(uuid.uuid4())
    handle.execute(
        "INSERT INTO memory_gt_known VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            known_id,
            query,
            answer,
            json.dumps(unique),
            basis,
            said_by,
            recorded_by,
            recorded_at,
            None if previous is None else previous["known_id"],
        ],
    )
    return known_id


def latest_known(handle: duckdb.DuckDBPyConnection, query: str) -> dict[str, Any] | None:
    """Return the known answer for a question that no later row supersedes, or None. Reads only."""
    row = handle.execute(
        "SELECT known_id, query, answer, right_events, basis, said_by, recorded_by, recorded_at "
        "FROM memory_gt_current_known WHERE query = ?",
        [query],
    ).fetchone()
    if row is None:
        return None
    keys = (
        "known_id",
        "query",
        "answer",
        "right_events",
        "basis",
        "said_by",
        "recorded_by",
        "recorded_at",
    )
    known = dict(zip(keys, row, strict=True))
    known["right_events"] = json.loads(known["right_events"])
    return known


def record_run(
    handle: duckdb.DuckDBPyConnection,
    *,
    case_id: str,
    query: str,
    tier: str,
    ran_at: str,
    settings: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Record, once, the settings in force and the reports (words, second stage, held-back facts) of a run.

    Goes in: the case id, the question, the tier, the time, the search settings and a report holding
    whatever else the search measured (the cross-scorer's scores, the keyword ranking, decay, facts held
    back). Recording the identical run again is a no-op; a different one for the same case raises
    ValueError because nothing is overwritten. Raises ValueError for an unknown case.
    """
    if (
        handle.execute("SELECT 1 FROM memory_gt_cases WHERE case_id = ?", [case_id]).fetchone()
        is None
    ):
        raise ValueError(f"Unknown case {case_id}")
    encoded = (json.dumps(settings, sort_keys=True), json.dumps(report, sort_keys=True))
    stored = handle.execute(
        "SELECT settings, report FROM memory_gt_runs WHERE case_id = ?", [case_id]
    ).fetchone()
    if stored is not None:
        if tuple(stored) != encoded:
            raise ValueError(f"Case {case_id} already has a different run record")
        return
    handle.execute(
        "INSERT INTO memory_gt_runs VALUES (?, ?, ?, ?, ?, ?)",
        [case_id, query, tier, ran_at, *encoded],
    )


def outputs_of(
    handle: duckdb.DuckDBPyConnection, case_id: str, as_of: str | None = None
) -> list[dict[str, Any]]:
    """Return a case's recorded outputs, models first, in a stable order; as_of limits them in time.

    With as_of, only outputs recorded at or before that instant are returned,
    which is how an earlier export is read again. Reads only.
    """
    rows = handle.execute(
        "SELECT output_key, role, model_id, revision, width, output, combiner_id, recorded_at "
        "FROM memory_gt_outputs WHERE case_id = ? AND (CAST(? AS VARCHAR) IS NULL OR "
        "CAST(recorded_at AS TIMESTAMPTZ) <= CAST(? AS TIMESTAMPTZ)) "
        "ORDER BY role DESC, output_key",
        [case_id, as_of, as_of],
    ).fetchall()
    keys = ("key", "role", "model_id", "revision", "width", "output", "combiner_id", "recorded_at")
    result = []
    for row in rows:
        item = dict(zip(keys, row, strict=True))
        item["output"] = json.loads(item["output"])
        result.append(item)
    return result


def record_judgment(
    handle: duckdb.DuckDBPyConnection,
    *,
    case_id: str,
    judged_by: str,
    judged_at: str,
    verdict: str,
    chosen_items: list[str],
    typed_answer: str | None,
    note: str | None,
) -> str:
    """Append a judgment that supersedes the case's current one, and return its id.

    Raises ValueError for an unknown case or verdict, a "right" or "pick" verdict
    with no chosen item, a "typed" verdict with no typed answer, or a chosen item
    that is not one the case's recorded outputs name. Earlier judgments are never
    edited; the new row points at the one it supersedes.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"Unknown verdict {verdict!r}")
    if (
        handle.execute("SELECT 1 FROM memory_gt_cases WHERE case_id = ?", [case_id]).fetchone()
        is None
    ):
        raise ValueError(f"Unknown case {case_id}")
    if verdict in SCORABLE_VERDICTS and not chosen_items:
        raise ValueError(f"A {verdict!r} judgment needs the chosen item or items")
    if verdict == "typed" and not typed_answer:
        raise ValueError("A typed judgment needs the typed answer")
    named = {
        entry["item_id"]
        for output in outputs_of(handle, case_id)
        if isinstance(output["output"], list)
        for entry in output["output"]
    }
    stray = [item for item in chosen_items if named and item not in named]
    if stray:
        raise ValueError(f"Chosen items {stray} are not named by any recorded output")
    previous = latest_judgment(handle, case_id)
    judgment_id = str(uuid.uuid4())
    handle.execute(
        "INSERT INTO memory_gt_judgments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            judgment_id,
            case_id,
            judged_by,
            judged_at,
            verdict,
            json.dumps(list(chosen_items)),
            typed_answer,
            note,
            None if previous is None else previous["judgment_id"],
        ],
    )
    return judgment_id


def judgments_of(
    handle: duckdb.DuckDBPyConnection, case_id: str, as_of: str | None = None
) -> list[dict[str, Any]]:
    """Return every judgment of a case, oldest first, including superseded ones; as_of limits them in time.

    With as_of, only judgments made at or before that instant are returned.
    Reads only.
    """
    rows = handle.execute(
        "SELECT judgment_id, case_id, judged_by, judged_at, verdict, chosen_items, typed_answer, "
        "note, supersedes FROM memory_gt_judgments WHERE case_id = ? AND "
        "(CAST(? AS VARCHAR) IS NULL OR CAST(judged_at AS TIMESTAMPTZ) <= CAST(? AS TIMESTAMPTZ)) "
        "ORDER BY CAST(judged_at AS TIMESTAMPTZ), rowid",
        [case_id, as_of, as_of],
    ).fetchall()
    keys = (
        "judgment_id",
        "case_id",
        "judged_by",
        "judged_at",
        "verdict",
        "chosen_items",
        "typed_answer",
        "note",
        "supersedes",
    )
    result = []
    for row in rows:
        item = dict(zip(keys, row, strict=True))
        item["chosen_items"] = json.loads(item["chosen_items"])
        result.append(item)
    return result


def latest_judgment(handle: duckdb.DuckDBPyConnection, case_id: str) -> dict[str, Any] | None:
    """Return the judgment no later judgment supersedes, or None if the case is unjudged. Reads only."""
    chain = judgments_of(handle, case_id)
    superseded = {j["supersedes"] for j in chain}
    current = [j for j in chain if j["judgment_id"] not in superseded]
    return current[-1] if current else None


def list_case_ids(
    handle: duckdb.DuckDBPyConnection, split: str | None = None, memory_kind: str | None = None
) -> list[str]:
    """Return case ids in creation order, optionally of one split and one memory kind. Reads only."""
    conditions, params = [], []
    if split is not None:
        conditions.append("split = ?")
        params.append(split)
    if memory_kind is not None:
        conditions.append("memory_kind = ?")
        params.append(memory_kind)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = handle.execute(
        f"SELECT case_id FROM memory_gt_cases {where} ORDER BY created_at, case_id", params
    ).fetchall()
    return [row[0] for row in rows]


def scorable_held_back(
    handle: duckdb.DuckDBPyConnection, memory_kind: str | None = None
) -> list[dict[str, Any]]:
    """Return the held-back cases a combiner can be scored on, with their correct items.

    A case qualifies when it is held back, its source was not removed, and its
    latest judgment names the correct items ("right" or "pick"). Each entry has
    the case id, memory kind, input, the per-model rankings (label -> item ids)
    and the set of correct item ids. Training cases are never returned. Reads only.
    """
    result = []
    for case_id in list_case_ids(handle, SPLIT_HELD_BACK, memory_kind):
        case = case_row(handle, case_id)
        judgment = latest_judgment(handle, case_id)
        if (
            case["source_removed"]
            or judgment is None
            or judgment["verdict"] not in SCORABLE_VERDICTS
        ):
            continue
        rankings = {
            out["key"]: [entry["item_id"] for entry in out["output"]]
            for out in outputs_of(handle, case_id)
            if out["role"] == MODEL_OUTPUT
        }
        result.append(
            {
                "case_id": case_id,
                "memory_kind": case["memory_kind"],
                "input": case["input"],
                "rankings": rankings,
                "correct": set(judgment["chosen_items"]),
            }
        )
    return result


def _export_content(
    handle: duckdb.DuckDBPyConnection, purpose: str, case_ids: list[str], as_of: str
) -> list:
    """Build the content an export of these cases carries, for fingerprinting and for the run.

    A training export carries each case, its events (pointers), its outputs with
    the combiner current at the time, and every judgment. A held-back-inputs
    export carries only the case id, memory kind, input and the model outputs,
    with no judgment and no split-revealing field beyond the id. Only outputs
    and judgments recorded at or before as_of are carried, so the same export
    can be read again later.
    """
    content = []
    for case_id in case_ids:
        case = case_row(handle, case_id)
        outputs = outputs_of(handle, case_id, as_of)
        if purpose == "training":
            content.append(
                {**case, "outputs": outputs, "judgments": judgments_of(handle, case_id, as_of)}
            )
        else:
            content.append(
                {
                    "case_id": case_id,
                    "memory_kind": case["memory_kind"],
                    "input_kind": case["input_kind"],
                    "input": case["input"],
                    "events": case["events"],
                    "outputs": [o for o in outputs if o["role"] == MODEL_OUTPUT],
                }
            )
    return content


def fingerprint_of(content: list) -> str:
    """Return the SHA-256 of the canonical JSON of an export's content; pure."""
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def export_manifest(
    handle: duckdb.DuckDBPyConnection, purpose: str, created_by: str, created_at: str
) -> dict[str, Any]:
    """Record and return an export manifest with its content, so a run can be repeated exactly.

    A "training" export lists the training cases whose source was not removed
    and never a held-back case. A "held_back_inputs" export lists the held-back
    cases (inputs and model outputs, no judgments) for a builder to merge. The
    manifest stores the sorted case ids and the content fingerprint. Raises
    ValueError for an unknown purpose.
    """
    if purpose not in EXPORT_PURPOSES:
        raise ValueError(f"Unknown export purpose {purpose!r}")
    split = SPLIT_TRAINING if purpose == "training" else SPLIT_HELD_BACK
    case_ids = [
        case_id
        for case_id in list_case_ids(handle, split)
        if not case_row(handle, case_id)["source_removed"]
    ]
    content = _export_content(handle, purpose, case_ids, created_at)
    manifest = {
        "export_id": str(uuid.uuid4()),
        "purpose": purpose,
        "case_ids": sorted(case_ids),
        "fingerprint": fingerprint_of(content),
        "created_at": created_at,
        "created_by": created_by,
    }
    handle.execute(
        "INSERT INTO memory_gt_exports VALUES (?, ?, ?, ?, ?, ?)",
        [
            manifest["export_id"],
            purpose,
            json.dumps(manifest["case_ids"]),
            manifest["fingerprint"],
            created_at,
            created_by,
        ],
    )
    return {**manifest, "content": content}


def verify_export(handle: duckdb.DuckDBPyConnection, export_id: str) -> dict[str, Any]:
    """Re-read an earlier export's cases and say whether its content is exactly as fingerprinted.

    Comes out: {"matches": bool, "removed": [case ids whose source has since been
    removed], "fingerprint_now": ...}. A removed source changes the content, so a
    repeat needs a new export; nothing is hidden. Raises ValueError for an unknown
    export. Reads only.
    """
    row = handle.execute(
        "SELECT purpose, case_ids, fingerprint FROM memory_gt_exports WHERE export_id = ?",
        [export_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown export {export_id}")
    purpose, encoded, fingerprint = row
    case_ids = json.loads(encoded)
    removed = [c for c in case_ids if case_row(handle, c)["source_removed"]]
    made_at = handle.execute(
        "SELECT created_at FROM memory_gt_exports WHERE export_id = ?", [export_id]
    ).fetchone()[0]
    now = fingerprint_of(_export_content(handle, purpose, case_ids, made_at))
    return {"matches": now == fingerprint, "removed": removed, "fingerprint_now": now}
