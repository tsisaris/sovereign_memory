"""The record of every language-model step the memory service ran: what DSPy will learn from.

It is for keeping, append-only, one row for each model step run through the service's "model" operation
(Graphiti ingest and add_triplet, Mem0 fact extraction, Zep summaries, Letta compaction, Cognee
completion, and the rest): which part and step, which opinion, the model asked for and the model that
really answered, the arguments, the output or the error, how long it took, who asked and the events the
arguments named. It takes a handle on the owner's open transaction and the facts of one run; it gives
back nothing. A prompt optimiser reads these rows through the ground_truth_table operation to build its
examples and to see which step fails or runs slowly. It never calls a model, never edits or deletes a
row, and a run is recorded whether it worked or failed.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_model_runs.py | a durable log of model steps for prompt optimisation | DSPy's own trace and history (in-process, lost at exit), Consensus log_run records (ecosystem-wide, not memory-scoped), MLflow tracing, Letta's run/step tables (SQLite/Postgres ORM) | One append-only DuckDB table beside the ground-truth tables, read through the existing table operation, so DSPy has one doorway and no second store |
"""

import json
from typing import Any

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_model_runs (
    run_id VARCHAR PRIMARY KEY,
    recorded_at VARCHAR NOT NULL,
    recorded_by VARCHAR NOT NULL,
    part VARCHAR NOT NULL,
    step VARCHAR NOT NULL,
    opinion VARCHAR NOT NULL,
    requested_model VARCHAR,
    achieved_model VARCHAR,
    seconds DOUBLE,
    ok BOOLEAN NOT NULL,
    args VARCHAR NOT NULL,
    output VARCHAR,
    error VARCHAR,
    event_ids VARCHAR NOT NULL
);
"""


def initialize_model_runs_schema(handle: duckdb.DuckDBPyConnection) -> None:
    """Create the model-runs table if it is not there yet; safe to call at every start."""
    handle.execute(SCHEMA)


def record_model_run(
    handle: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    recorded_at: str,
    recorded_by: str,
    part: str,
    step: str,
    opinion: str,
    requested_model: str | None,
    achieved_model: str | None,
    seconds: float | None,
    args: dict[str, Any],
    output: dict[str, Any] | None,
    error: str | None,
) -> None:
    """Append one run to the record: ok is true exactly when there is an output and no error.

    The events the arguments name (event_ids and event_id) are kept apart so a reader can leave out
    the runs of events it may not see. Raises ValueError when a run has both or neither of output
    and error, because a record that cannot say whether the step worked is worthless.
    """
    if (output is None) == (error is None):
        raise ValueError(
            "a model run has either an output or an error, never both and never neither"
        )
    named = list(args.get("event_ids", []))
    if isinstance(args.get("event_id"), str):
        named.append(args["event_id"])
    handle.execute(
        "INSERT INTO memory_model_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            recorded_at,
            recorded_by,
            part,
            step,
            opinion,
            requested_model,
            achieved_model,
            seconds,
            error is None,
            json.dumps(args, default=str, sort_keys=True),
            None if output is None else json.dumps(output, default=str, sort_keys=True),
            error,
            json.dumps(sorted(set(named))),
        ],
    )
