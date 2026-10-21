"""Open native local DuckDB storage without silently adopting another database engine."""

import os
import re
from pathlib import Path

import duckdb
from duckdb_engine import ConnectionWrapper
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool


def require_native_connection(connection):
    """Verify a borrowed handle uses native storage without committing or closing it."""
    databases = connection.execute(
        "SELECT type FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchall()
    if any(database_type != "duckdb" for (database_type,) in databases):
        raise duckdb.NotSupportedError(
            "Only native DuckDB storage is supported; an explicit database migration is required"
        )


def connect_native(database=":memory:", *args, **kwargs):
    """Open a native file or memory database while preserving legacy SQLite files unchanged.

    DuckDB can automatically open SQLite through an extension. Reject its exact
    header before connecting, and reject storage-engine prefixes. Missing files
    follow DuckDB's normal creation path; other filesystem failures remain errors.
    """
    database = os.fspath(database)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9_+.-]+:", database):
        raise duckdb.NotSupportedError("Native DuckDB requires a plain local database path")
    try:
        with Path(database).open("rb") as source:
            header = source.read(16)
    except FileNotFoundError:
        # No input file exists: native DuckDB will create it or open its memory database.
        header = b""
    except OSError as error:
        raise duckdb.IOException(str(error)) from error
    if header == b"SQLite format 3\x00":
        raise duckdb.NotSupportedError(
            "SQLite storage is unsupported; explicit migration to DuckDB is required. "
            "The original file has not been modified."
        )
    connection = duckdb.connect(database, *args, **kwargs)
    try:
        require_native_connection(connection)
    except Exception:
        connection.close()
        raise
    return connection


class BorrowedNativeHandle(ConnectionWrapper):
    """Present a native DuckDB handle to SQLAlchemy without letting it own the handle.

    SQLAlchemy's begin, rollback and close become no-ops and commit is refused,
    so statements run inside whatever transaction the handle's owner holds and
    only that owner finishes it.
    """

    def begin(self) -> None:
        """Do nothing: the handle's owner has already begun or will begin it."""

    def commit(self) -> None:
        """Refuse: only the handle's owner may commit."""
        raise duckdb.TransactionException("A borrowed DuckDB handle is committed only by its owner")

    def rollback(self) -> None:
        """Do nothing: the handle's owner rolls back its own failed transaction."""

    def close(self) -> None:
        """Do nothing: the handle's owner closes it."""


def borrowed_engine(native: duckdb.DuckDBPyConnection) -> Engine:
    """Return a SQLAlchemy engine whose only connection runs on native.

    The engine never opens another connection, never resets the handle when a
    session ends and never commits, rolls back or closes it, and it does not
    change the handle's current schema.
    """
    return create_engine(
        "duckdb://",
        creator=lambda: BorrowedNativeHandle(native),
        poolclass=StaticPool,
        pool_reset_on_return=None,
    )
