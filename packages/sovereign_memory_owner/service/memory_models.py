"""The model registry and the per-model numbers tables.

A model is a row of data: id, revision, width, kind, the supplier's collection
and recipe, and the date it was registered. DuckDB arrays have a fixed size per
column, so every model revision owns one numbers table whose name is derived
from its registry row; the registry is the only place that maps a model to its
table. Memory names no model in code. This module creates and reads those rows
and tables inside the caller's transaction; it never makes numbers, never runs a
model, never opens or commits a transaction itself and never touches an
original event.
"""

import hashlib
import math
from dataclasses import dataclass

import duckdb

from service.memory_supplier import MODEL_KINDS

NUMBERS_TABLE_PREFIX = "memory_numbers_"
MODEL_TABLE_DIGEST_LENGTH = 20

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_models (
    model_id VARCHAR NOT NULL,
    revision VARCHAR NOT NULL,
    width INTEGER NOT NULL,
    kind VARCHAR NOT NULL,
    supplier_collection VARCHAR NOT NULL,
    supplier_recipe VARCHAR NOT NULL,
    registered_at VARCHAR NOT NULL,
    numbers_table VARCHAR NOT NULL UNIQUE,
    PRIMARY KEY (model_id, revision)
);
CREATE TABLE IF NOT EXISTS memory_model_checks (
    model_id VARCHAR NOT NULL,
    revision VARCHAR NOT NULL,
    checked_at VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    supplier_model_id VARCHAR,
    supplier_revision VARCHAR,
    supplier_width INTEGER,
    reason VARCHAR
);
"""


@dataclass(frozen=True)
class ModelRow:
    """One registered model revision, exactly as its registry row stores it.

    kind is "embedding" or "classifier"; width is the count of numbers per text
    (the class count for a classifier); numbers_table is derived from the row.
    """

    model_id: str
    revision: str
    width: int
    kind: str
    supplier_collection: str
    supplier_recipe: str
    registered_at: str
    numbers_table: str

    @property
    def label(self) -> str:
        """Return the model's label "id@revision", used wherever a model is named in output."""
        return model_label(self.model_id, self.revision)


@dataclass(frozen=True)
class ModelState:
    """Whether one model is fresh for search, and if not exactly why not.

    reasons is empty exactly when the model is fresh. The counts are what the
    reasons were measured from: numbers rows held, rows marked stale and live
    event pieces that have no numbers row for this model.
    """

    model: ModelRow
    fresh: bool
    reasons: tuple[str, ...]
    rows: int
    stale_rows: int
    missing_pieces: int


def model_label(model_id: str, revision: str) -> str:
    """Return "id@revision", the one way output names a model revision; pure."""
    return f"{model_id}@{revision}"


def numbers_table_name(model_id: str, revision: str) -> str:
    """Derive the numbers table name of a model revision from its label; pure.

    The name is a fixed prefix plus a digest, so it is always a safe identifier
    and no model is named in code. Only the registry row stores the mapping.
    """
    digest = hashlib.sha256(f"{model_id}\x1f{revision}".encode()).hexdigest()
    return NUMBERS_TABLE_PREFIX + digest[:MODEL_TABLE_DIGEST_LENGTH]


def _numbers_schema(table: str, width: int) -> str:
    """Return the CREATE TABLE statement of one model's numbers table; width is a checked integer."""
    return f"""
    CREATE TABLE IF NOT EXISTS {table} (
        event_id VARCHAR NOT NULL,
        piece_id VARCHAR PRIMARY KEY,
        model_id VARCHAR NOT NULL,
        revision VARCHAR NOT NULL,
        width INTEGER NOT NULL,
        text_fingerprint VARCHAR NOT NULL,
        received_at VARCHAR NOT NULL,
        stale BOOLEAN NOT NULL DEFAULT FALSE,
        stale_reason VARCHAR,
        numbers FLOAT[{int(width)}] NOT NULL
    )
    """


def initialize_registry_schema(handle: duckdb.DuckDBPyConnection) -> None:
    """Create the registry and label-check tables if absent, inside the caller's transaction."""
    handle.execute(REGISTRY_SCHEMA)


def _row(values: tuple) -> ModelRow:
    """Build a ModelRow from a memory_models row in column order."""
    return ModelRow(*values)


_COLUMNS = (
    "model_id, revision, width, kind, supplier_collection, supplier_recipe, "
    "registered_at, numbers_table"
)


def register_model(
    handle: duckdb.DuckDBPyConnection,
    *,
    model_id: str,
    revision: str,
    width: int,
    kind: str,
    supplier_collection: str,
    supplier_recipe: str,
    registered_at: str,
) -> ModelRow:
    """Add a model revision to the registry and create its numbers table; idempotent.

    Registering the identical row again returns the stored row unchanged.
    Raises ValueError for an empty name, a non-integer or non-positive width,
    an unknown kind, or a model revision already registered with a different
    label (a revision's width, kind and supplier never change; a new revision
    is a new row). It never receives numbers and touches no other model.
    """
    for name, value in (
        ("model_id", model_id),
        ("revision", revision),
        ("supplier_collection", supplier_collection),
        ("supplier_recipe", supplier_recipe),
        ("registered_at", registered_at),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"A model needs a non-empty {name}")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("A model's width must be a positive integer")
    if kind not in MODEL_KINDS:
        raise ValueError(f"A model's kind must be one of {MODEL_KINDS}, not {kind!r}")
    table = numbers_table_name(model_id, revision)
    stored = handle.execute(
        f"SELECT {_COLUMNS} FROM memory_models WHERE model_id = ? AND revision = ?",
        [model_id, revision],
    ).fetchone()
    if stored is not None:
        found = _row(stored)
        if (found.width, found.kind, found.supplier_collection, found.supplier_recipe) != (
            width,
            kind,
            supplier_collection,
            supplier_recipe,
        ):
            raise ValueError(
                f"Model {model_label(model_id, revision)} is already registered with another "
                "width, kind or supplier; register a new revision instead"
            )
        return found
    handle.execute(_numbers_schema(table, width))
    handle.execute(
        "INSERT INTO memory_models VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            model_id,
            revision,
            width,
            kind,
            supplier_collection,
            supplier_recipe,
            registered_at,
            table,
        ],
    )
    return ModelRow(
        model_id, revision, width, kind, supplier_collection, supplier_recipe, registered_at, table
    )


def list_models(handle: duckdb.DuckDBPyConnection, kind: str | None = None) -> list[ModelRow]:
    """Return the registered models in registration order, optionally of one kind. Reads only."""
    where, params = ("WHERE kind = ?", [kind]) if kind is not None else ("", [])
    rows = handle.execute(
        f"SELECT {_COLUMNS} FROM memory_models {where} ORDER BY registered_at, model_id, revision",
        params,
    ).fetchall()
    return [_row(row) for row in rows]


def get_model(handle: duckdb.DuckDBPyConnection, model_id: str, revision: str) -> ModelRow | None:
    """Return one registered model revision, or None when it is not registered. Reads only."""
    row = handle.execute(
        f"SELECT {_COLUMNS} FROM memory_models WHERE model_id = ? AND revision = ?",
        [model_id, revision],
    ).fetchone()
    return None if row is None else _row(row)


def model_state(handle: duckdb.DuckDBPyConnection, model: ModelRow) -> ModelState:
    """Measure whether a model is fresh, from its label check and its numbers table. Reads only.

    A model is stale when the supplier's current row for it no longer matches
    its label, when any of its rows is marked stale, when a live event piece has
    no row for it, or when it holds no numbers at all. A stale model is stale
    for itself only; the answer names every reason found.
    """
    table = model.numbers_table
    check = handle.execute(
        "SELECT status, reason FROM memory_model_checks WHERE model_id = ? AND revision = ? "
        "ORDER BY checked_at DESC, rowid DESC LIMIT 1",
        [model.model_id, model.revision],
    ).fetchone()
    (rows,) = handle.execute(f"SELECT count(*) FROM {table}").fetchone()
    (stale_rows,) = handle.execute(f"SELECT count(*) FROM {table} WHERE stale").fetchone()
    (missing,) = handle.execute(
        f"SELECT count(*) FROM memory_pieces p LEFT JOIN memory_removals r "
        f"ON r.target_event_id = p.event_id LEFT JOIN {table} n ON n.piece_id = p.piece_id "
        "WHERE p.source = 'event' AND r.target_event_id IS NULL AND n.piece_id IS NULL"
    ).fetchone()
    reasons = []
    if check is not None and check[0] == "mismatch":
        reasons.append(f"the supplier's current row no longer matches this label: {check[1]}")
    if stale_rows:
        reasons.append(f"{stale_rows} of its rows are marked stale")
    if missing:
        reasons.append(f"{missing} live pieces have no numbers for this model")
    if rows == 0:
        reasons.append("no numbers have been received for this model yet")
    return ModelState(model, not reasons, tuple(reasons), rows, stale_rows, missing)


def check_vector(values: object, width: int) -> list[float]:
    """Return values as a list of floats after checking its length and that every number is finite.

    Raises ValueError for anything else. It never pads, truncates or repairs.
    """
    if not isinstance(values, (list, tuple)) or len(values) != width:
        raise ValueError(f"Numbers must be a list of exactly {width} values")
    cleaned = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Numbers must all be numeric")
        try:
            finite = math.isfinite(value)
        except OverflowError as error:
            raise ValueError("A number is too large to store") from error
        if not finite:
            raise ValueError("Numbers must all be finite")
        cleaned.append(float(value))
    return cleaned
