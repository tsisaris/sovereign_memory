"""dedupe cache_session_context and add unique entry index.

Revision ID: c3d5e7f9a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-07-27 00:00:00.000000

One-time cleanup for the SQL session-cache backend: duplicate
(user_id, session_id, entry_id) rows accumulated because
create_session_context_entry() appended instead of upserting (racing
update-then-create writers, e.g. the session persist watermark). Keep the
newest row per key — updates rewrite payload in place, so max(seq) carries the
latest state — then add the unique index the adapter's ON CONFLICT upsert
targets.

Cache tables are create-on-init (cognee/infrastructure/databases/cache/sql/
tables.py), not alembic-managed: fresh databases get the index from the table
definition, so this migration only heals pre-existing tables. It covers the
database it is connected to, and the standalone SQLite cache file (the default
sqlite cache.db lives next to the relational SQLite database) by resolving its
location the same way the runtime does. There is no runtime fallback: if a
cache database exists that this migration cannot update — including a split
non-SQLite cache via CACHE_DB_URL — the migration fails, so a deployment
cannot proceed with a cache database the upsert would break on.
"""

import logging
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "cache_session_context"
INDEX_NAME = "uq_cache_session_context_entry"
KEY_COLUMNS = ("user_id", "session_id", "entry_id")

MANUAL_STATEMENTS = (
    f"DELETE FROM {TABLE_NAME} WHERE seq NOT IN "
    f"(SELECT MAX(seq) FROM {TABLE_NAME} GROUP BY {', '.join(KEY_COLUMNS)}); "
    f"CREATE UNIQUE INDEX {INDEX_NAME} ON {TABLE_NAME} ({', '.join(KEY_COLUMNS)});"
)


def dedupe_session_context_entries(conn) -> None:
    """Drop all but the newest (max seq) row per (user_id, session_id, entry_id)."""
    table = sa.table(
        TABLE_NAME,
        sa.column("seq"),
        sa.column("user_id"),
        sa.column("session_id"),
        sa.column("entry_id"),
    )
    newest = sa.select(sa.func.max(table.c.seq)).group_by(
        table.c.user_id, table.c.session_id, table.c.entry_id
    )
    conn.execute(sa.delete(table).where(table.c.seq.notin_(newest)))


def create_session_context_unique_index(conn) -> None:
    """Create the unique index the adapter's ON CONFLICT upsert targets."""
    conn.execute(
        sa.text(f"CREATE UNIQUE INDEX {INDEX_NAME} ON {TABLE_NAME} ({', '.join(KEY_COLUMNS)})")
    )


TURSO_DRIVER = "cognee_turso"


def standalone_sqlite_cache_path(alembic_conn) -> tuple[str, str] | None:
    """Path and driver of the standalone SQLite-family cache database to fix.

    Returns ``(path, driver)``; the driver is ``aiosqlite`` for stock SQLite and
    ``cognee_turso`` for a cache on the Turso rewrite engine, whose files (in
    ``mvcc`` journal mode) a plain SQLite reader cannot open — see ``_connect_turso_cache``.

    Resolves the location exactly as the runtime does (CACHE_DB_URL when set,
    else a cache.db next to the relational SQLite database). Returns None when
    no separate cache database needs handling: the SQL cache backend is not in
    use, the cache lives in the database this migration is already connected
    to, or the SQLite file does not exist yet (a fresh database gets the index
    from the table definition — never create the file from a migration).

    Raises RuntimeError for a split non-SQLite cache database (CACHE_DB_URL):
    this migration cannot reach it, and proceeding would deploy a cache the
    upsert breaks on — apply the statements there manually, then re-run.
    """
    from cognee.infrastructure.databases.cache.config import get_cache_config
    from cognee.infrastructure.databases.cache.get_cache_engine import _resolve_cache_db_url
    from cognee.infrastructure.databases.exceptions import CacheConnectionError
    from sqlalchemy.engine import make_url

    cache_config = get_cache_config()
    if cache_config.cache_backend not in ("sqlite", "turso", "postgres"):
        return None

    try:
        url = make_url(_resolve_cache_db_url(cache_config.cache_backend, cache_config.cache_db_url))
    except CacheConnectionError:
        # Mirror get_cache_engine: the implicit sqlite default degrades to the
        # filesystem cache on deployments it cannot serve (e.g. system root on
        # S3) — no SQL cache database exists there, so nothing to migrate. An
        # explicitly chosen backend fails loudly at runtime too, so keep failing.
        explicitly_chosen = "cache_backend" in cache_config.model_fields_set
        if cache_config.cache_backend != "sqlite" or explicitly_chosen:
            raise
        return None
    alembic_url = alembic_conn.engine.url

    if url.get_backend_name() != "sqlite":
        same_database = (
            url.get_backend_name() == alembic_url.get_backend_name()
            and url.host == alembic_url.host
            and url.port == alembic_url.port
            and url.database == alembic_url.database
        )
        if same_database:
            return None
        raise RuntimeError(
            f"The session cache lives in a separate database this migration cannot "
            f"reach (CACHE_DB_URL={url.render_as_string(hide_password=True)}). "
            f"Apply the following there manually, then re-run: {MANUAL_STATEMENTS}"
        )

    if not url.database:
        return None
    path = os.path.abspath(url.database)
    if alembic_url.database and os.path.abspath(alembic_url.database) == path:
        return None
    if not os.path.exists(path):
        return None
    return path, url.get_driver_name()


CACHE_ALIAS = "standalone_cache"


def _connect_turso_cache(path: str):
    """Open a standalone Turso cache file through pyturso.

    A Turso cache is not opened as a SQLite file: an ``mvcc``-mode file is not a
    SQLite database to a plain SQLite reader ("file is not a database"), and the
    migration must not fail on upgrade. The connection exposes a DB-API surface.
    """
    # Without the turso extra there is no way to heal a Turso cache, and the
    # runtime cache engine would fail to start anyway; fail here with the
    # install hint rather than a bare ModuleNotFoundError.
    from cognee.infrastructure.databases.turso.runtime import INSTALL_HINT, explain_file_in_use

    try:
        import turso
    except ImportError as error:
        raise RuntimeError(
            f"The session cache at {path} lives on the Turso engine (CACHE_BACKEND=turso "
            f"or a sqlite+cognee_turso CACHE_DB_URL) but pyturso is not installed. "
            f"{INSTALL_HINT}"
        ) from error

    with explain_file_in_use(path):
        connection = turso.connect(path)
        connection.execute("PRAGMA busy_timeout=30000").fetchall()
    return connection


def _attach_sqlite_cache(path: str):
    """Open a standalone SQLite cache file through DuckDB's own sqlite extension.

    Returns a DuckDB connection with the file attached read-write as
    ``standalone_cache``. DuckDB's extension reads and writes SQLite files, so
    this migration needs no SQLite library of its own. Raises RuntimeError naming
    the file when the extension is not installed for this DuckDB.
    """
    import duckdb

    connection = duckdb.connect()
    try:
        connection.execute("LOAD sqlite")
    except duckdb.Error as error:
        connection.close()
        raise RuntimeError(
            f"The session cache at {path} is a SQLite file and DuckDB's sqlite extension "
            f"is not installed, so it cannot be healed: {error}"
        ) from error
    quoted = path.replace("'", "''")
    connection.execute(f"ATTACH '{quoted}' AS {CACHE_ALIAS} (TYPE sqlite)")
    return connection


def _heal_turso_cache(path: str) -> None:
    """Dedupe and index the Turso cache file; it is opened and always closed here."""
    connection = _connect_turso_cache(path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)
        ).fetchone()
        index_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)
        ).fetchone()
        if table_exists and not index_exists:
            connection.execute(
                f"DELETE FROM {TABLE_NAME} WHERE seq NOT IN "
                f"(SELECT MAX(seq) FROM {TABLE_NAME} GROUP BY {', '.join(KEY_COLUMNS)})"
            )
            connection.execute(
                f"CREATE UNIQUE INDEX {INDEX_NAME} ON {TABLE_NAME} ({', '.join(KEY_COLUMNS)})"
            )
            connection.commit()
            logger.info("Deduped and indexed standalone SQLite cache database at %s", path)
    finally:
        connection.close()


def _heal_sqlite_cache(path: str) -> None:
    """Dedupe and index a stock SQLite cache file through DuckDB's sqlite extension.

    The file is attached, changed in one transaction and always released. Nothing
    is done when the table is missing or the index already exists.
    """
    connection = _attach_sqlite_cache(path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM duckdb_tables() WHERE database_name = ? AND table_name = ?",
            [CACHE_ALIAS, TABLE_NAME],
        ).fetchone()
        index_exists = connection.execute(
            "SELECT 1 FROM duckdb_indexes() WHERE database_name = ? AND index_name = ?",
            [CACHE_ALIAS, INDEX_NAME],
        ).fetchone()
        if table_exists and not index_exists:
            table = f"{CACHE_ALIAS}.main.{TABLE_NAME}"
            connection.begin()
            connection.execute(
                f"DELETE FROM {table} WHERE seq NOT IN "
                f"(SELECT MAX(seq) FROM {table} GROUP BY {', '.join(KEY_COLUMNS)})"
            )
            connection.execute(
                f"CREATE UNIQUE INDEX {INDEX_NAME} ON {table} ({', '.join(KEY_COLUMNS)})"
            )
            connection.commit()
            logger.info("Deduped and indexed standalone SQLite cache database at %s", path)
    finally:
        connection.close()


def heal_standalone_sqlite_cache(path: str, driver: str = "aiosqlite") -> None:
    """Apply the same dedupe-then-index to the standalone cache.db file.

    A Turso cache is opened through pyturso, any other file through DuckDB's sqlite
    extension. Any failure propagates and fails the migration: there is no runtime
    fallback, so a cache database left without the index must block the deploy.
    """
    if driver == TURSO_DRIVER:
        _heal_turso_cache(path)
    else:
        _heal_sqlite_cache(path)


def drop_standalone_sqlite_cache_index(path: str, driver: str = "aiosqlite") -> None:
    """Downgrade mirror: the pre-index adapter appends duplicate keys, so the
    unique index must not survive a downgrade or old writes raise IntegrityError."""
    if driver == TURSO_DRIVER:
        connection = _connect_turso_cache(path)
        try:
            connection.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
            connection.commit()
        finally:
            connection.close()
        return
    connection = _attach_sqlite_cache(path)
    try:
        connection.execute(f"DROP INDEX IF EXISTS {CACHE_ALIAS}.main.{INDEX_NAME}")
    finally:
        connection.close()


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if TABLE_NAME in inspector.get_table_names() and INDEX_NAME not in {
        index["name"] for index in inspector.get_indexes(TABLE_NAME)
    }:
        dedupe_session_context_entries(conn)
        create_session_context_unique_index(conn)

    standalone = standalone_sqlite_cache_path(conn)
    if standalone is not None:
        heal_standalone_sqlite_cache(*standalone)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if TABLE_NAME in inspector.get_table_names() and INDEX_NAME in {
        index["name"] for index in inspector.get_indexes(TABLE_NAME)
    }:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

    standalone = standalone_sqlite_cache_path(conn)
    if standalone is not None:
        drop_standalone_sqlite_cache_index(*standalone)
