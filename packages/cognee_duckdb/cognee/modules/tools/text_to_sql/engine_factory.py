"""Async engines for authorized external databases, read-only by construction.

Deliberately NOT :class:`SQLAlchemyAdapter`: that adapter's ``execute_query``
commits, which is wrong for a read-only external database. These engines carry
read-only settings at the connection level so the protection applies to every
statement, whatever the SQL guard missed. A SQLite file is read through DuckDB's
own sqlite extension, so no SQLite package is needed: the file is attached to an
in-memory DuckDB connection, read-only unless the approval-gated write path asks
otherwise, and the connection is then locked against reading other files.
"""

import os
import re
from functools import lru_cache

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from cognee.modules.tools.errors import ToolError
from cognee.shared.lru_cache import DATABASE_MAX_LRU_CACHE_SIZE

_POSTGRES_SCHEME_RE = ("postgres://", "postgresql://", "postgresql+psycopg2://")
_SQLITE_SCHEME_RE = ("sqlite://", "sqlite+aiosqlite://")
_SQLITE_CATALOG = "external"


def _normalize_url(connection_string: str, provider: str) -> str:
    """Rewrite a postgres DSN onto the async driver; refuse any other provider.

    SQLite does not come through here: see :func:`_sqlite_engine`.
    """
    if provider == "postgres":
        for scheme in _POSTGRES_SCHEME_RE:
            if connection_string.startswith(scheme):
                return "postgresql+asyncpg://" + connection_string[len(scheme) :]
        if connection_string.startswith("postgresql+asyncpg://"):
            return connection_string
        raise ToolError(f"Unrecognized postgres connection string scheme: {connection_string[:30]}")

    raise ToolError(f"Unsupported tool connection provider: {provider}")


def _sqlite_file(connection_string: str) -> str:
    """Return the path of the SQLite file a ``sqlite://`` DSN names, refusing what cannot be read.

    ``sqlite:///name.db`` is relative and ``sqlite:////abs/name.db`` absolute. A
    ``file:`` URI is refused, and so is a path that is not an existing file, because
    attaching a missing file in write mode would create an empty database.
    """
    for scheme in _SQLITE_SCHEME_RE:
        if connection_string.startswith(scheme):
            path = connection_string[len(scheme) :]
            break
    else:
        raise ToolError(f"Unrecognized sqlite connection string scheme: {connection_string[:30]}")
    if path.startswith("/file:"):
        raise ToolError("A sqlite file: URI is not supported; give the path of the file")
    path = path.lstrip("/") if not path.startswith("//") else path[1:]
    if not os.path.isfile(path):
        raise ToolError(f"The sqlite file {path} does not exist")
    return os.path.abspath(path)


def _sqlite_engine(connection_string: str, read_only: bool) -> AsyncEngine:
    """Return an engine on an in-memory DuckDB connection with the SQLite file attached.

    Every new connection loads DuckDB's sqlite extension, attaches the file (read-only
    when asked) as the current catalog, keeps a copy of the file's table definitions
    for schema introspection, then switches off access to other files and locks the
    settings, so a generated query can only see the attached database.
    """
    quoted = _sqlite_file(connection_string).replace("'", "''")
    mode = ", READ_ONLY" if read_only else ""
    engine = create_async_engine("duckdb+aio:///:memory:", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def attach(dbapi_connection, _record) -> None:
        """Attach the SQLite file to a fresh connection and lock the connection down."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("LOAD sqlite")
            cursor.execute(f"ATTACH '{quoted}' AS {_SQLITE_CATALOG} (TYPE sqlite{mode})")
            cursor.execute(f"USE {_SQLITE_CATALOG}")
            cursor.execute(
                "CREATE TEMP TABLE sqlite_definitions AS SELECT name, sql FROM "
                f"sqlite_scan('{quoted}', 'sqlite_master') WHERE type = 'table'"
            )
            cursor.execute("SET enable_external_access = false")
            cursor.execute("SET lock_configuration = true")
        finally:
            cursor.close()

    return engine


@lru_cache(maxsize=DATABASE_MAX_LRU_CACHE_SIZE)
def get_external_sql_engine(
    connection_string: str, provider: str, statement_timeout_ms: int
) -> AsyncEngine:
    """A cached read-only engine for one external database.

    NullPool: recall-time tool calls are low-frequency, and holding no idle
    connections to someone's production database is the polite default.
    """
    if provider == "sqlite":
        return _sqlite_engine(connection_string, read_only=True)

    url = _normalize_url(connection_string, provider)

    connect_args: dict = {}
    if provider == "postgres":
        connect_args["server_settings"] = {
            "default_transaction_read_only": "on",
            "statement_timeout": str(int(statement_timeout_ms)),
        }

    return create_async_engine(url, poolclass=NullPool, connect_args=connect_args)


@lru_cache(maxsize=DATABASE_MAX_LRU_CACHE_SIZE)
def get_external_sql_write_engine(
    connection_string: str, provider: str, statement_timeout_ms: int
) -> AsyncEngine:
    """A cached WRITABLE engine — used only by the approval-gated write path.

    Nothing else may import this: reads go through the read-only engine, and
    a write proposal only reaches this engine after the deployment gate, the
    per-connection ``allow_writes`` flag, and (for apply) an explicit
    approval call.
    """
    if provider == "sqlite":
        return _sqlite_engine(connection_string, read_only=False)

    url = _normalize_url(connection_string, provider)

    connect_args: dict = {}
    if provider == "postgres":
        connect_args["server_settings"] = {
            "statement_timeout": str(int(statement_timeout_ms)),
        }

    return create_async_engine(url, poolclass=NullPool, connect_args=connect_args)


def _split_top_level(body: str) -> list[str]:
    """Split a table body on the commas that are not inside parentheses."""
    parts, depth, start = [], 0, 0
    for index, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return [part.strip() for part in parts if part.strip()]


def _names(listing: str) -> list[str]:
    """Return the identifiers of a comma separated list, with their quotes removed."""
    return [name.strip().strip("\"`[]'") for name in listing.split(",") if name.strip()]


def _sqlite_foreign_keys(definition: str) -> list[dict]:
    """Read the foreign keys out of a SQLite ``CREATE TABLE`` text.

    DuckDB's sqlite extension does not report foreign keys, so they are read from the
    table's own definition: column level ``REFERENCES`` and table level ``FOREIGN KEY``.
    """
    body = definition[definition.find("(") + 1 : definition.rfind(")")]
    keys = []
    for part in _split_top_level(body):
        table_level = re.match(
            r"(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*REFERENCES\s+"
            r"(?P<table>\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|\w+)\s*(?:\((?P<refs>[^)]*)\))?",
            part,
            re.IGNORECASE,
        )
        if table_level:
            keys.append(
                {
                    "column": ", ".join(_names(table_level.group("cols"))),
                    "ref_table": _names(table_level.group("table"))[0],
                    "ref_column": ", ".join(_names(table_level.group("refs") or "")),
                }
            )
            continue
        column_level = re.match(
            r"(?P<col>\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|\w+)\s.*?\bREFERENCES\s+"
            r"(?P<table>\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|\w+)\s*(?:\((?P<refs>[^)]*)\))?",
            part,
            re.IGNORECASE | re.DOTALL,
        )
        if column_level:
            keys.append(
                {
                    "column": _names(column_level.group("col"))[0],
                    "ref_table": _names(column_level.group("table"))[0],
                    "ref_column": ", ".join(_names(column_level.group("refs") or "")),
                }
            )
    return keys


def _introspect_attached_sqlite(sync_connection, allowed_tables, max_tables) -> dict:
    """Table → columns/PK/FK map for a SQLite file attached to a DuckDB connection."""
    table_names = [
        row[0]
        for row in sync_connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_catalog = :catalog "
                "AND table_type = 'BASE TABLE' ORDER BY table_name"
            ),
            {"catalog": _SQLITE_CATALOG},
        )
    ]
    if allowed_tables:
        allowed = set(allowed_tables)
        table_names = [name for name in table_names if name in allowed]
    table_names = table_names[: int(max_tables)]

    schema: dict = {}
    for table_name in table_names:
        columns = [
            {"name": row[0], "type": row[1]}
            for row in sync_connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_catalog = :catalog AND table_name = :table "
                    "ORDER BY ordinal_position"
                ),
                {"catalog": _SQLITE_CATALOG, "table": table_name},
            )
        ]
        primary_key: list = []
        for row in sync_connection.execute(
            text(
                "SELECT constraint_column_names FROM duckdb_constraints() WHERE "
                "database_name = :catalog AND table_name = :table "
                "AND constraint_type = 'PRIMARY KEY'"
            ),
            {"catalog": _SQLITE_CATALOG, "table": table_name},
        ):
            primary_key = list(row[0])
        definition = sync_connection.execute(
            text("SELECT sql FROM temp.main.sqlite_definitions WHERE name = :table"),
            {"table": table_name},
        ).scalar()
        schema[table_name] = {
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": _sqlite_foreign_keys(definition) if definition else [],
        }
    return schema


async def introspect_schema(
    engine: AsyncEngine,
    allowed_tables: list[str] | None = None,
    max_tables: int = 50,
) -> dict:
    """Table → columns/PK/FK map for the prompt, capped and filterable.

    Uses SQLAlchemy inspection for Postgres, and DuckDB's own catalog for a SQLite
    file read through DuckDB.
    """
    from sqlalchemy import inspect

    def _inspect(sync_connection):
        if sync_connection.dialect.name == "duckdb":
            return _introspect_attached_sqlite(sync_connection, allowed_tables, max_tables)
        inspector = inspect(sync_connection)
        table_names = inspector.get_table_names()
        if allowed_tables:
            allowed = set(allowed_tables)
            table_names = [name for name in table_names if name in allowed]
        table_names = sorted(table_names)[: int(max_tables)]

        schema: dict = {}
        for table_name in table_names:
            columns = [
                {"name": column["name"], "type": str(column["type"])}
                for column in inspector.get_columns(table_name)
            ]
            pk_constraint = inspector.get_pk_constraint(table_name) or {}
            primary_key = pk_constraint.get("constrained_columns") or []
            foreign_keys = [
                {
                    "column": ", ".join(fk.get("constrained_columns") or []),
                    "ref_table": fk.get("referred_table"),
                    "ref_column": ", ".join(fk.get("referred_columns") or []),
                }
                for fk in inspector.get_foreign_keys(table_name)
            ]
            schema[table_name] = {
                "columns": columns,
                "primary_key": primary_key,
                "foreign_keys": foreign_keys,
            }
        return schema

    async with engine.connect() as connection:
        return await connection.run_sync(_inspect)
