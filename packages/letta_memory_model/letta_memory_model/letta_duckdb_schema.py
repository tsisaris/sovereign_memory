"""Create and check Letta's reserved DuckDB schema on a supplied native handle.

Letta's tables live only in the schema named by LETTA_SCHEMA. This module
creates that schema and its tables, refuses an existing table whose shape
differs from the mapped one (for example a historical BINARY embedding column),
and reports same-named tables in other schemas without touching them. It never
changes a connection's default schema, never drops, renames or converts a table,
and never begins, commits, rolls back or closes the handle it is given.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from duckdb_sqlalchemy_async.native import borrowed_engine
from sqlalchemy import Connection, text

from letta_memory_model.orm import Base
from letta_memory_model.orm.base import LETTA_SCHEMA

LegacyPolicy = Literal["refuse", "preserve"]


class LettaSchemaError(RuntimeError):
    """Raised when existing storage cannot be used as Letta's schema unchanged.

    differences lists one plain sentence per problem found. Nothing has been
    created, changed or deleted when it is raised.
    """

    def __init__(self, message: str, differences: list[str]):
        """Keep the message and the exact differences found."""
        super().__init__(message + ": " + "; ".join(differences))
        self.differences = differences


@dataclass(frozen=True)
class LegacyTable:
    """A table outside LETTA_SCHEMA that carries a Letta table's name.

    letta_shaped says whether it has every column of the mapped Letta table
    (another engine's table of the same name, such as Mem0's main.messages,
    does not). embedding_type is the stored type of its embedding column, or
    None when it has none. It describes the table; it never changes it.
    """

    schema: str
    table: str
    row_count: int
    letta_shaped: bool
    embedding_type: Optional[str]


# The shared borrowed-handle engine lives in the foundation; the old name stays importable.
engine_for = borrowed_engine


def _expected_columns(connection: Connection) -> dict[str, dict[str, str]]:
    """Return each mapped table's column names and DuckDB type names."""
    dialect = connection.dialect
    return {
        table.name: {
            column.name: column.type.compile(dialect=dialect).upper() for column in table.columns
        }
        for table in Base.metadata.sorted_tables
    }


def _stored_columns(connection: Connection, schema: str, table: str) -> dict[str, str]:
    """Return a stored table's column names and DuckDB type names, empty if absent."""
    rows = connection.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_catalog = current_database() AND table_schema = :schema "
            "AND table_name = :table ORDER BY ordinal_position"
        ),
        {"schema": schema, "table": table},
    ).all()
    return {name: data_type.upper() for name, data_type in rows}


def schema_differences(connection: Connection) -> list[str]:
    """Return how existing tables in LETTA_SCHEMA differ from the mapped tables.

    An absent table is not a difference (it will be created). A missing or
    extra column, or a column of another type, is. It only reads.
    """
    differences = []
    for table, expected in _expected_columns(connection).items():
        stored = _stored_columns(connection, LETTA_SCHEMA, table)
        if not stored:
            continue
        for column in expected.keys() - stored.keys():
            differences.append(f"{LETTA_SCHEMA}.{table} lacks column {column}")
        for column in stored.keys() - expected.keys():
            differences.append(f"{LETTA_SCHEMA}.{table} has unmapped column {column}")
        for column in expected.keys() & stored.keys():
            if expected[column] != stored[column]:
                differences.append(
                    f"{LETTA_SCHEMA}.{table}.{column} is {stored[column]}, "
                    f"expected {expected[column]}"
                )
    return differences


def find_legacy_tables(connection: Connection) -> list[LegacyTable]:
    """Report tables in other schemas of this database that carry Letta table names.

    It reads only; tables are reported in schema and name order.
    """
    expected = _expected_columns(connection)
    rows = connection.execute(
        text(
            "SELECT schema_name, table_name FROM duckdb_tables() "
            "WHERE database_name = current_database() AND schema_name <> :schema "
            "ORDER BY schema_name, table_name"
        ),
        {"schema": LETTA_SCHEMA},
    ).all()
    found = []
    for schema, table in rows:
        if table not in expected:
            continue
        stored = _stored_columns(connection, schema, table)
        preparer = connection.dialect.identifier_preparer
        quoted = f"{preparer.quote_schema(schema)}.{preparer.quote(table)}"
        count = connection.execute(text(f"SELECT count(*) FROM {quoted}")).scalar_one()
        found.append(
            LegacyTable(
                schema=schema,
                table=table,
                row_count=count,
                letta_shaped=expected[table].keys() <= stored.keys(),
                embedding_type=stored.get("embedding"),
            )
        )
    return found


def ensure_schema(connection: Connection, legacy: LegacyPolicy = "refuse") -> list[LegacyTable]:
    """Create LETTA_SCHEMA and its missing tables after checking what already exists.

    connection is a SQLAlchemy connection on the caller's transaction. legacy
    "refuse" raises LettaSchemaError when a Letta-shaped table exists outside
    LETTA_SCHEMA, because its rows would otherwise be silently left behind;
    "preserve" leaves such tables untouched and returns them. An existing
    LETTA_SCHEMA table of another shape always raises LettaSchemaError, before
    anything is created. It returns the legacy tables found.
    """
    if legacy not in ("refuse", "preserve"):
        raise ValueError(f"Unknown legacy table policy {legacy!r}")
    differences = schema_differences(connection)
    if differences:
        raise LettaSchemaError(
            "Existing Letta tables differ from the mapped schema and need an explicit "
            "migration; nothing was changed",
            differences,
        )
    legacy_tables = find_legacy_tables(connection)
    shaped = [table for table in legacy_tables if table.letta_shaped]
    if shaped and legacy == "refuse":
        raise LettaSchemaError(
            "Historical Letta tables exist outside the reserved schema and need an "
            "explicit preserving migration; nothing was changed",
            [
                f"{table.schema}.{table.table} holds {table.row_count} rows"
                + (f", embedding {table.embedding_type}" if table.embedding_type else "")
                for table in shaped
            ],
        )
    Base.metadata.create_all(connection, checkfirst=True)
    return legacy_tables
