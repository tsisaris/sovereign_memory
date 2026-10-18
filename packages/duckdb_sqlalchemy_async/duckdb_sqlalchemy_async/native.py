"""Open native local DuckDB storage without silently adopting another database engine."""

import os
import re
from pathlib import Path

import duckdb


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
