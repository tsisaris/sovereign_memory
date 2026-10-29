"""Pieces and received numbers: storing what the supplier hands over, per model.

A piece is the unit the supplier cut: its id, its event, its position in that
event and its text. Numbers arrive labelled with model, revision and width and
are held in that model's own table (see memory_models). This module receives a
batch into one model's table, marks a model's rows stale, looks numbers up by
text, keeps the durable list of numbers still owed by the supplier, and pulls
what the supplier can hand over. It never makes numbers, never measures or cuts
text, never calls a model and never changes an original event or another
model's rows. The functions that take a handle run inside the caller's
transaction; NumbersStore runs them as single owner-worker jobs.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import duckdb
from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_models import (
    ModelRow,
    ModelState,
    check_vector,
    get_model,
    initialize_registry_schema,
    list_models,
    model_label,
    model_state,
    register_model,
)
from service.memory_supplier import (
    PIECE_SOURCE_DERIVED,
    PIECE_SOURCE_EVENT,
    NumbersNotYetSupplied,
    NumbersSupplier,
    SuppliedNumbers,
    SuppliedPiece,
    text_fingerprint,
)

NUMBERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_pieces (
    piece_id VARCHAR PRIMARY KEY,
    event_id VARCHAR NOT NULL,
    position INTEGER NOT NULL,
    text VARCHAR NOT NULL,
    text_fingerprint VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    received_at VARCHAR NOT NULL,
    FOREIGN KEY (event_id) REFERENCES memory_events(event_id)
);
CREATE TABLE IF NOT EXISTS memory_number_requests (
    request_id VARCHAR PRIMARY KEY,
    model_id VARCHAR NOT NULL,
    revision VARCHAR NOT NULL,
    event_id VARCHAR,
    text VARCHAR,
    text_fingerprint VARCHAR,
    reason VARCHAR NOT NULL,
    requested_at VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    closed_at VARCHAR
);
CREATE TABLE IF NOT EXISTS memory_piece_retirements (
    piece_id VARCHAR PRIMARY KEY,
    retired_at VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    replaced_by VARCHAR
);
"""

RECUT_DUTY = (
    "a re-cut of an event must be sent as one batch holding the event's complete new set of "
    "pieces, with 'retires' naming exactly the event's current piece ids; memory then retires "
    "the old pieces (their numbers stay, but they no longer rank or count) and asks every model "
    "for the new pieces"
)


def derived_piece_id(event_id: str, fingerprint: str) -> str:
    """Return the piece id of a text derived from an event: keyed by the event and the text's fingerprint; pure."""
    return f"derived:{event_id}:{fingerprint}"


def _now() -> str:
    """Return the current UTC instant as ISO 8601 text."""
    return datetime.now(timezone.utc).isoformat()


def initialize_numbers_schema(handle: duckdb.DuckDBPyConnection) -> None:
    """Create the registry, pieces and request tables if absent, inside the caller's transaction."""
    initialize_registry_schema(handle)
    handle.execute(NUMBERS_SCHEMA)


def receive_numbers(
    handle: duckdb.DuckDBPyConnection, batch: SuppliedNumbers, received_at: str
) -> dict[str, int]:
    """Store one supplied batch into its model's table, replacing only that model's rows.

    Goes in: a batch labelled with model, revision and width, and the receive
    time. Comes out: counts {"pieces_new", "rows_new", "rows_replaced"}. Raises
    ValueError, storing nothing from the batch, when the model is not
    registered, the label's width differs from the registry row, a piece names
    an unknown event, a piece's fingerprint is not that of its text, a piece id
    was already stored with other content, a piece has no numbers, or a vector
    is the wrong length or not finite. Rows of other models, the originals and
    events are never touched.
    """
    model = get_model(handle, batch.model_id, batch.revision)
    if model is None:
        raise ValueError(
            f"Model {model_label(batch.model_id, batch.revision)} is not registered; "
            "adding a model is adding a registry row first"
        )
    if batch.width != model.width:
        raise ValueError(
            f"Batch width {batch.width} differs from the registered width {model.width} "
            f"of {model.label}"
        )
    piece_ids = {piece.piece_id for piece in batch.pieces}
    if set(batch.numbers) != piece_ids:
        raise ValueError("The batch must carry numbers for exactly its pieces")
    counts = {"pieces_new": 0, "rows_new": 0, "rows_replaced": 0}
    _check_cut_rules(handle, batch)
    for piece in batch.pieces:
        if piece.source not in (PIECE_SOURCE_EVENT, PIECE_SOURCE_DERIVED):
            raise ValueError(f"Piece {piece.piece_id} has an unknown source {piece.source!r}")
        if text_fingerprint(piece.text) != piece.text_fingerprint:
            raise ValueError(f"Piece {piece.piece_id} fingerprint is not that of its text")
        if piece.source == PIECE_SOURCE_DERIVED and piece.piece_id != derived_piece_id(
            piece.event_id, piece.text_fingerprint
        ):
            raise ValueError(
                f"A derived piece is keyed by its event and its text fingerprint: expected "
                f"{derived_piece_id(piece.event_id, piece.text_fingerprint)}, got {piece.piece_id}"
            )
        if (
            handle.execute(
                "SELECT 1 FROM memory_events WHERE event_id = ?", [piece.event_id]
            ).fetchone()
            is None
        ):
            raise ValueError(f"Piece {piece.piece_id} names unknown event {piece.event_id}")
        stored = handle.execute(
            "SELECT event_id, position, text_fingerprint, source FROM memory_pieces "
            "WHERE piece_id = ?",
            [piece.piece_id],
        ).fetchone()
        if stored is None:
            counts["pieces_new"] += 1
        elif stored != (piece.event_id, piece.position, piece.text_fingerprint, piece.source):
            raise ValueError(f"Piece {piece.piece_id} was already stored with other content")
    vectors = {pid: check_vector(batch.numbers[pid], model.width) for pid in piece_ids}
    _retire_recut(handle, batch, received_at)
    for piece in batch.pieces:
        handle.execute(
            "INSERT OR IGNORE INTO memory_pieces VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                piece.piece_id,
                piece.event_id,
                piece.position,
                piece.text,
                piece.text_fingerprint,
                piece.source,
                received_at,
            ],
        )
        existed = handle.execute(
            f"SELECT 1 FROM {model.numbers_table} WHERE piece_id = ?", [piece.piece_id]
        ).fetchone()
        handle.execute(
            f"INSERT OR REPLACE INTO {model.numbers_table} "
            "(event_id, piece_id, model_id, revision, width, text_fingerprint, received_at, "
            "stale, stale_reason, numbers) VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, NULL, ?)",
            [
                piece.event_id,
                piece.piece_id,
                model.model_id,
                model.revision,
                model.width,
                piece.text_fingerprint,
                received_at,
                vectors[piece.piece_id],
            ],
        )
        counts["rows_replaced" if existed else "rows_new"] += 1
        close_requests(handle, model, piece.event_id, piece.text_fingerprint, received_at)
    return counts


def live_event_piece_ids(handle: duckdb.DuckDBPyConnection, event_id: str) -> set[str]:
    """Return the ids of the event's pieces cut from its own text that are not retired. Reads only."""
    rows = handle.execute(
        "SELECT p.piece_id FROM memory_pieces p LEFT JOIN memory_piece_retirements r "
        "ON r.piece_id = p.piece_id WHERE p.event_id = ? AND p.source = 'event' "
        "AND r.piece_id IS NULL",
        [event_id],
    ).fetchall()
    return {row[0] for row in rows}


def _check_cut_rules(handle: duckdb.DuckDBPyConnection, batch: SuppliedNumbers) -> None:
    """Refuse a batch whose pieces of an event disagree with the event's stored cut, naming the supplier's duty.

    An event's first cut must arrive whole and with no 'retires'. Later batches
    for another model must carry exactly the same piece ids. A different set of
    piece ids is a re-cut, accepted only as RECUT_DUTY says. Raises ValueError.
    """
    by_event: dict[str, set[str]] = {}
    for piece in batch.pieces:
        if piece.source == PIECE_SOURCE_EVENT:
            by_event.setdefault(piece.event_id, set()).add(piece.piece_id)
    retires = set(batch.retires)
    seen_retires: set[str] = set()
    for event_id, ids in by_event.items():
        stored = live_event_piece_ids(handle, event_id)
        if not stored:
            continue
        if ids == stored:
            if retires & stored:
                raise ValueError(f"Event {event_id}: 'retires' may not name the pieces it sends")
            continue
        if retires & stored != stored:
            raise ValueError(
                f"Event {event_id} is already cut into {sorted(stored)} and the batch sends "
                f"{sorted(ids)}; {RECUT_DUTY}"
            )
        seen_retires |= stored
    stray = retires - seen_retires
    if stray:
        raise ValueError(
            f"'retires' names pieces that are not a current cut being replaced: {sorted(stray)}"
        )


def _retire_recut(
    handle: duckdb.DuckDBPyConnection, batch: SuppliedNumbers, retired_at: str
) -> None:
    """Retire the pieces a re-cut replaces and send the event's relational receipt back to pending.

    Runs after the batch passed every check. The old pieces and their numbers
    are kept (nothing is deleted); they simply stop ranking and counting. The
    relational receipt is set to pending so the tier drops the old chunks and
    writes the new ones through the ordinary processor path.
    """
    if not batch.retires:
        return
    replacement = ",".join(
        sorted(p.piece_id for p in batch.pieces if p.source == PIECE_SOURCE_EVENT)
    )
    for old in batch.retires:
        event_id = handle.execute(
            "SELECT event_id FROM memory_pieces WHERE piece_id = ?", [old]
        ).fetchone()[0]
        handle.execute(
            "INSERT OR IGNORE INTO memory_piece_retirements VALUES (?, ?, ?, ?)",
            [old, retired_at, "the supplier re-cut the event", replacement],
        )
        handle.execute(
            "UPDATE memory_receipts SET status = 'pending', attempt_id = NULL, attempted_at = NULL, "
            "completed_at = NULL, error_ref = NULL WHERE event_id = ? AND tier = 'relational'",
            [event_id],
        )


def retire_derived(
    handle: duckdb.DuckDBPyConnection, event_id: str, text: str, retired_at: str
) -> bool:
    """Retire the derived piece of a text an engine no longer holds; return whether one was retired.

    A Mem0 memory whose text was updated or deleted no longer needs numbers for
    the old text, so its derived piece stops counting as missing for any model.
    Nothing is deleted; no piece for that text means nothing to retire.
    """
    piece_id = derived_piece_id(event_id, text_fingerprint(text))
    found = handle.execute("SELECT 1 FROM memory_pieces WHERE piece_id = ?", [piece_id]).fetchone()
    if found is None:
        return False
    handle.execute(
        "INSERT OR IGNORE INTO memory_piece_retirements VALUES (?, ?, ?, NULL)",
        [piece_id, retired_at, "the engine no longer holds this text"],
    )
    return True


def mark_stale(
    handle: duckdb.DuckDBPyConnection,
    model: ModelRow,
    reason: str,
    piece_ids: list[str] | None = None,
) -> int:
    """Mark one model's rows (all of them, or the named pieces) stale and return how many changed.

    Only that model's table is written; no other model, no piece and no original
    is touched. The rows stay until the supplier's numbers replace them.
    """
    if not reason:
        raise ValueError("A stale mark needs its reason")
    if piece_ids is None:
        found = handle.execute(
            f"SELECT count(*) FROM {model.numbers_table} WHERE NOT stale"
        ).fetchone()
        handle.execute(
            f"UPDATE {model.numbers_table} SET stale = TRUE, stale_reason = ? WHERE NOT stale",
            [reason],
        )
        return found[0]
    changed = 0
    for piece_id in piece_ids:
        (count,) = handle.execute(
            f"SELECT count(*) FROM {model.numbers_table} WHERE piece_id = ? AND NOT stale",
            [piece_id],
        ).fetchone()
        handle.execute(
            f"UPDATE {model.numbers_table} SET stale = TRUE, stale_reason = ? "
            "WHERE piece_id = ? AND NOT stale",
            [reason, piece_id],
        )
        changed += count
    return changed


def record_label_check(
    handle: duckdb.DuckDBPyConnection,
    model: ModelRow,
    status: str,
    reason: str | None,
    supplier_row: tuple[str, str, int] | None,
    checked_at: str,
) -> None:
    """Append one comparison of a model's label with the supplier's current row (append-only).

    status is "current" (labels equal), "mismatch" (they differ) or "unverified"
    (the supplier could not answer). supplier_row is the supplier's (model id,
    revision, width) or None. Earlier checks stay.
    """
    model_id, revision, width = supplier_row if supplier_row else (None, None, None)
    handle.execute(
        "INSERT INTO memory_model_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [model.model_id, model.revision, checked_at, status, model_id, revision, width, reason],
    )


def numbers_for_text(
    handle: duckdb.DuckDBPyConnection, model: ModelRow, text: str
) -> list[float] | None:
    """Return the received, not stale numbers of one model for a text, or None if none were received.

    The text is matched by its fingerprint. It never makes a vector: None means
    "numbers not yet supplied". Reads only.
    """
    row = handle.execute(
        f"SELECT numbers FROM {model.numbers_table} WHERE text_fingerprint = ? AND NOT stale "
        "ORDER BY received_at DESC LIMIT 1",
        [text_fingerprint(text)],
    ).fetchone()
    return None if row is None else [float(value) for value in row[0]]


def request_numbers(
    handle: duckdb.DuckDBPyConnection,
    model: ModelRow,
    event_id: str | None,
    text: str | None,
    reason: str,
    requested_at: str,
) -> str:
    """Record, durably, that numbers (or pieces, when text is None) are owed by the supplier.

    Asking again for the same model, event and text while the earlier request
    is still pending returns the earlier request's id and writes nothing. The
    text, when given, is what the supplier is asked to measure; it is cleared
    when the request is closed.
    """
    fingerprint = None if text is None else text_fingerprint(text)
    existing = handle.execute(
        "SELECT request_id FROM memory_number_requests WHERE model_id = ? AND revision = ? "
        "AND event_id IS NOT DISTINCT FROM ? AND text_fingerprint IS NOT DISTINCT FROM ? "
        "AND status = 'pending'",
        [model.model_id, model.revision, event_id, fingerprint],
    ).fetchone()
    if existing is not None:
        return existing[0]
    request_id = str(uuid.uuid4())
    handle.execute(
        "INSERT INTO memory_number_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL)",
        [
            request_id,
            model.model_id,
            model.revision,
            event_id,
            text,
            fingerprint,
            reason,
            requested_at,
        ],
    )
    return request_id


def request_derived(
    handle: duckdb.DuckDBPyConnection,
    model: ModelRow,
    event_id: str,
    texts: list[str],
    reason: str,
    requested_at: str,
) -> list[str]:
    """Make the open requests for an event's derived texts exactly the texts now missing.

    A pending request for a text of this event and model that is no longer in
    texts (a retry produced other text) is closed as superseded, and its text is
    cleared, so nothing stays open for text that no longer exists. A request is
    recorded for each text in texts. Returns the request ids for texts.
    """
    wanted = {text_fingerprint(text) for text in texts}
    open_rows = handle.execute(
        "SELECT request_id, text_fingerprint FROM memory_number_requests WHERE model_id = ? "
        "AND revision = ? AND event_id = ? AND status = 'pending' AND text_fingerprint IS NOT NULL",
        [model.model_id, model.revision, event_id],
    ).fetchall()
    for request_id, fingerprint in open_rows:
        if fingerprint not in wanted:
            handle.execute(
                "UPDATE memory_number_requests SET status = 'superseded', closed_at = ?, "
                "text = NULL WHERE request_id = ?",
                [requested_at, request_id],
            )
    return [request_numbers(handle, model, event_id, t, reason, requested_at) for t in texts]


def close_derived_requests(
    handle: duckdb.DuckDBPyConnection, model: ModelRow, event_id: str, closed_at: str
) -> None:
    """Close every open derived-text request of an event and model after a retry found all its numbers."""
    handle.execute(
        "UPDATE memory_number_requests SET status = 'superseded', closed_at = ?, text = NULL "
        "WHERE model_id = ? AND revision = ? AND event_id = ? AND status = 'pending' "
        "AND text_fingerprint IS NOT NULL",
        [closed_at, model.model_id, model.revision, event_id],
    )


def withdraw_removed_requests(handle: duckdb.DuckDBPyConnection, closed_at: str) -> int:
    """Close every open request whose event was removed, clear its text, and return how many closed."""
    (count,) = handle.execute(
        "SELECT count(*) FROM memory_number_requests q JOIN memory_removals r "
        "ON r.target_event_id = q.event_id WHERE q.status = 'pending'"
    ).fetchone()
    handle.execute(
        "UPDATE memory_number_requests SET status = 'withdrawn', closed_at = ?, text = NULL "
        "WHERE status = 'pending' AND event_id IN (SELECT target_event_id FROM memory_removals)",
        [closed_at],
    )
    return count


def close_requests(
    handle: duckdb.DuckDBPyConnection,
    model: ModelRow,
    event_id: str,
    fingerprint: str,
    closed_at: str,
) -> None:
    """Close pending requests that a received row answers and clear the text they carried."""
    handle.execute(
        "UPDATE memory_number_requests SET status = 'answered', closed_at = ?, text = NULL "
        "WHERE model_id = ? AND revision = ? AND status = 'pending' AND "
        "(text_fingerprint = ? OR (text_fingerprint IS NULL AND event_id = ?))",
        [closed_at, model.model_id, model.revision, fingerprint, event_id],
    )


def pending_requests(handle: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """List the numbers still owed, oldest first, leaving out requests for removed events. Reads only."""
    rows = handle.execute(
        "SELECT q.request_id, q.model_id, q.revision, q.event_id, q.text, q.reason, "
        "q.requested_at FROM memory_number_requests q LEFT JOIN memory_removals r "
        "ON r.target_event_id = q.event_id WHERE q.status = 'pending' "
        "AND r.target_event_id IS NULL ORDER BY q.requested_at, q.request_id"
    ).fetchall()
    keys = ("request_id", "model_id", "revision", "event_id", "text", "reason", "requested_at")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def events_needing_numbers(handle: duckdb.DuckDBPyConnection, model: ModelRow) -> list[str]:
    """List live captured events for which this model lacks fresh numbers, in capture order.

    An event qualifies when it has no piece yet, or one of its pieces has no
    row, or only a stale row, in this model's table. Removed events and the
    owner's own system events never qualify. Reads only.
    """
    rows = handle.execute(
        f"""
        SELECT e.event_id FROM memory_events e
        LEFT JOIN memory_removals r ON r.target_event_id = e.event_id
        WHERE r.target_event_id IS NULL AND e.event_type NOT LIKE 'system.%'
          AND (
            NOT EXISTS (SELECT 1 FROM memory_pieces p
                        LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id
                        WHERE p.event_id = e.event_id AND p.source = 'event'
                          AND x.piece_id IS NULL)
            OR EXISTS (SELECT 1 FROM memory_pieces p
                       LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id
                       LEFT JOIN {model.numbers_table} n ON n.piece_id = p.piece_id
                       WHERE p.event_id = e.event_id AND p.source = 'event'
                         AND x.piece_id IS NULL AND (n.piece_id IS NULL OR n.stale)))
        ORDER BY e.rowid
        """
    ).fetchall()
    return [row[0] for row in rows]


def derived_needing_numbers(
    handle: duckdb.DuckDBPyConnection, model: ModelRow
) -> list[tuple[str, str]]:
    """List (event id, text) of live derived texts for which this model lacks fresh numbers.

    A derived text is live when its event was not removed and its piece was not
    retired. It lacks numbers when this model's table has no row, or only a
    stale row, for it. Reads only.
    """
    rows = handle.execute(
        f"SELECT p.event_id, p.text FROM memory_pieces p "
        "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
        "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
        f"LEFT JOIN {model.numbers_table} n ON n.piece_id = p.piece_id "
        "WHERE p.source = 'derived' AND x.piece_id IS NULL AND r.target_event_id IS NULL "
        "AND (n.piece_id IS NULL OR n.stale) ORDER BY p.received_at, p.piece_id"
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def pending_derived_texts(
    handle: duckdb.DuckDBPyConnection, model: ModelRow
) -> list[tuple[str, str]]:
    """List (event id, text) of open derived-text requests for this model whose event is live. Reads only."""
    rows = handle.execute(
        "SELECT q.event_id, q.text FROM memory_number_requests q LEFT JOIN memory_removals r "
        "ON r.target_event_id = q.event_id WHERE q.status = 'pending' AND q.model_id = ? "
        "AND q.revision = ? AND q.text IS NOT NULL AND r.target_event_id IS NULL "
        "ORDER BY q.requested_at, q.request_id",
        [model.model_id, model.revision],
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def retired_piece_ids_of_event(handle: duckdb.DuckDBPyConnection, event_id: str) -> list[str]:
    """Return the ids of the event's retired event-source pieces, so their chunks can be dropped. Reads only."""
    rows = handle.execute(
        "SELECT p.piece_id FROM memory_pieces p JOIN memory_piece_retirements x "
        "ON x.piece_id = p.piece_id WHERE p.event_id = ? AND p.source = 'event' "
        "ORDER BY p.piece_id",
        [event_id],
    ).fetchall()
    return [row[0] for row in rows]


def pieces_of_event(handle: duckdb.DuckDBPyConnection, event_id: str) -> list[dict[str, Any]]:
    """Return the event's current (not retired) pieces cut from its own text, in position order. Reads only."""
    rows = handle.execute(
        "SELECT p.piece_id, p.position, p.text, p.text_fingerprint FROM memory_pieces p "
        "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
        "WHERE p.event_id = ? AND p.source = 'event' AND x.piece_id IS NULL "
        "ORDER BY p.position, p.piece_id",
        [event_id],
    ).fetchall()
    keys = ("piece_id", "position", "text", "text_fingerprint")
    return [{"event_id": event_id, **dict(zip(keys, row, strict=True))} for row in rows]


def embedding_labels_and_states(
    handle: duckdb.DuckDBPyConnection,
) -> tuple[list[ModelState], list[ModelState]]:
    """Split the registered embedding models into (fresh, stale) states. Reads only."""
    states = [model_state(handle, model) for model in list_models(handle, "embedding")]
    return [s for s in states if s.fresh], [s for s in states if not s.fresh]


class NumbersStore:
    """The async face of the registry and the received numbers, on the owner's connection.

    Each method runs as one job on the owner's worker, in one transaction that
    commits or rolls back whole. It holds the connection only; it never makes
    numbers, never measures or cuts text and never calls a model.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        """Bind the owner's long-lived connection."""
        self.conn = conn

    async def run_in_transaction(self, work):
        """Run work(handle) inside one transaction on the owner's worker and return its result."""

        def job():
            """Begin, run, commit; roll back and re-raise on any failure."""
            handle = self.conn.native
            handle.begin()
            try:
                result = work(handle)
                handle.commit()
                return result
            except BaseException:
                handle.rollback()
                raise

        return await self.conn.run(job)

    async def initialize_schema(self) -> None:
        """Create the registry, pieces and request tables if they do not exist."""
        await self.run_in_transaction(initialize_numbers_schema)

    async def register(self, **fields: Any) -> ModelRow:
        """Register a model revision (see register_model); registered_at defaults to now."""
        fields.setdefault("registered_at", _now())
        return await self.run_in_transaction(lambda handle: register_model(handle, **fields))

    async def models(self, kind: str | None = None) -> list[ModelRow]:
        """Return the registered models, optionally of one kind."""
        return await self.run_in_transaction(lambda handle: list_models(handle, kind))

    async def states(self) -> list[ModelState]:
        """Return the freshness state of every registered model."""
        return await self.run_in_transaction(
            lambda handle: [model_state(handle, m) for m in list_models(handle)]
        )

    async def receive(self, batch: SuppliedNumbers) -> dict[str, int]:
        """Receive one supplied batch into its model's table (see receive_numbers)."""
        received_at = _now()
        return await self.run_in_transaction(
            lambda handle: receive_numbers(handle, batch, received_at)
        )

    async def mark_stale(self, model_id: str, revision: str, reason: str) -> int:
        """Mark every row of one registered model stale and return how many changed."""

        def work(handle):
            """Look up the model and mark its rows."""
            model = get_model(handle, model_id, revision)
            if model is None:
                raise ValueError(f"Model {model_label(model_id, revision)} is not registered")
            return mark_stale(handle, model, reason)

        return await self.run_in_transaction(work)

    async def request(
        self, model: ModelRow, event_id: str | None, text: str | None, reason: str
    ) -> str:
        """Record numbers (or pieces) as owed by the supplier and return the request id."""
        return await self.run_in_transaction(
            lambda handle: request_numbers(handle, model, event_id, text, reason, _now())
        )

    async def pending(self) -> list[dict[str, Any]]:
        """Return the numbers still owed by the supplier."""
        return await self.run_in_transaction(pending_requests)

    async def sync(self, supplier: NumbersSupplier) -> dict[str, Any]:
        """Compare every model's label with the supplier's, then pull what it can hand over.

        For each registered model: ask the supplier for its current row. If the
        supplier cannot answer, record the check as unverified and the pending
        work, and pull nothing. If the label no longer matches, mark that
        model's rows stale, record the mismatch, ask for recomputation and keep
        the request as pending work. If it matches, ask for the saved numbers of
        every event that lacks fresh ones and receive them; a refusal is kept as
        pending work. Returns {label: summary}. It writes no other model's rows.
        """
        report: dict[str, Any] = {}
        now = _now()
        await self.run_in_transaction(lambda handle: withdraw_removed_requests(handle, now))
        for model in await self.models():
            summary: dict[str, Any] = {"received_events": 0, "pending": None}
            report[model.label] = summary
            try:
                current = await supplier.current_row(
                    model.supplier_collection, model.supplier_recipe
                )
            except NumbersNotYetSupplied as pending:
                await self.run_in_transaction(
                    lambda h, m=model, p=pending: record_label_check(
                        h, m, "unverified", str(p), None, _now()
                    )
                )
                summary["pending"] = str(pending)
                continue
            supplied = (current.model_id, current.revision, current.width)
            if supplied != (model.model_id, model.revision, model.width):
                reason = (
                    f"supplier now runs {model_label(current.model_id, current.revision)} "
                    f"width {current.width} for this collection and recipe"
                )
                undelivered = await self._mismatch(supplier, model, supplied, reason)
                summary["pending"] = reason + (
                    f"; recompute request not delivered: {undelivered}" if undelivered else ""
                )
                continue
            await self.run_in_transaction(
                lambda h, m=model, s=supplied: record_label_check(h, m, "current", None, s, _now())
            )
            events = await self.run_in_transaction(lambda h, m=model: events_needing_numbers(h, m))
            for event_id in events:
                try:
                    batch = await supplier.saved_numbers(
                        model.supplier_collection,
                        model.supplier_recipe,
                        model.model_id,
                        model.revision,
                        event_id,
                    )
                except NumbersNotYetSupplied as pending:
                    await self.request(model, event_id, None, str(pending))
                    summary["pending"] = str(pending)
                    break
                await self.receive(batch)
                summary["received_events"] += 1
            if summary["pending"] is None:
                summary["received_derived"] = 0
                await self._pull_derived(supplier, model, summary)
        return report

    async def _pull_derived(
        self, supplier: NumbersSupplier, model: ModelRow, summary: dict[str, Any]
    ) -> None:
        """Fetch this model's numbers for every live derived text it lacks, through the single-text door.

        Each text becomes the one derived piece keyed by its event and fingerprint
        (memory cuts nothing: the text is measured whole). A refusal is kept as
        pending work and stops this model's pull.
        """
        wanted = await self.run_in_transaction(
            lambda h: list(
                dict.fromkeys(derived_needing_numbers(h, model) + pending_derived_texts(h, model))
            )
        )
        for event_id, text in wanted:
            try:
                vector = await supplier.question_numbers(
                    model.model_id, model.revision, text, "passage"
                )
            except NumbersNotYetSupplied as pending:
                await self.request(model, event_id, text, str(pending))
                summary["pending"] = str(pending)
                return
            fingerprint = text_fingerprint(text)
            piece_id = derived_piece_id(event_id, fingerprint)
            piece = SuppliedPiece(piece_id, event_id, 0, text, fingerprint, PIECE_SOURCE_DERIVED)
            await self.receive(
                SuppliedNumbers(
                    model.model_id, model.revision, model.width, (piece,), {piece_id: vector}
                )
            )
            summary["received_derived"] += 1

    async def _mismatch(
        self,
        supplier: NumbersSupplier,
        model: ModelRow,
        supplied: tuple[str, str, int],
        reason: str,
    ) -> str | None:
        """Mark one model stale for a label mismatch, record the check and ask for recomputation.

        Only that model is touched. Returns None when the supplier took the
        request, else the supplier's refusal text; the request stays recorded as
        pending work either way.
        """

        def work(handle):
            """Mark the model's rows stale and append the mismatch check, in one transaction."""
            mark_stale_count = mark_stale(handle, model, reason)
            record_label_check(handle, model, "mismatch", reason, supplied, _now())
            request_numbers(handle, model, None, None, reason, _now())
            return mark_stale_count

        await self.run_in_transaction(work)
        events = await self.run_in_transaction(lambda h: events_needing_numbers(h, model))
        try:
            await supplier.recompute(
                model.supplier_collection,
                model.supplier_recipe,
                model.model_id,
                model.revision,
                events,
                reason,
            )
        except NumbersNotYetSupplied as pending:
            return str(pending)
        return None
