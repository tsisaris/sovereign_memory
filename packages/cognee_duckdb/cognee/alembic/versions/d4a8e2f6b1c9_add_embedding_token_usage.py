"""Add embedding token usage columns (SDK-1077).

Revision ID: d4a8e2f6b1c9
Revises: f6b8d0a2c4e6
Create Date: 2026-10-13 00:00:00.000000

Embedding tokens are stored apart from LLM tokens, never added into them:
pipeline_runs.embedding_tokens per operation (nullable, NULL = not measured,
like tokens_in), session_records.embedding_tokens / embedding_cost_usd per
session, and session_model_usage.kind, so an embedding model's per-model row
is told apart from an LLM's. Session columns are NOT NULL with a server
default that is also correct for existing rows: embeddings were never counted
before, and every existing per-model row is an LLM's.

Idempotent: every add/drop is inspector-guarded, like the other revisions.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a8e2f6b1c9"
down_revision: str | None = "f6b8d0a2c4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column name, SQLAlchemy type factory, nullable, server default)
_NEW_COLUMNS = [
    ("pipeline_runs", "embedding_tokens", lambda: sa.Integer(), True, None),
    ("session_records", "embedding_tokens", lambda: sa.Integer(), False, "0"),
    ("session_records", "embedding_cost_usd", lambda: sa.Float(), False, "0"),
    ("session_model_usage", "kind", lambda: sa.String(), False, "llm"),
]


def upgrade() -> None:
    """Add measured token fields while preserving defaults for existing records."""
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())

    for table, name, make_type, nullable, server_default in _NEW_COLUMNS:
        if table in tables and not _has_column(insp, table, name):
            column = sa.Column(name, make_type(), nullable=nullable, server_default=server_default)
            _add_column(table, column)


def downgrade() -> None:
    """Remove token fields from existing tables through the declared backend."""
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())

    for table, name, *_ in _NEW_COLUMNS:
        if table in tables and _has_column(insp, table, name):
            _drop_column(table, name)


def _has_column(inspector, table: str, name: str) -> bool:
    """Read existing column names without inferring migration completion."""
    return any(column["name"] == name for column in inspector.get_columns(table))


def _add_column(table: str, column: sa.Column) -> None:
    """Add a column, separating DuckDB's default and nullability operations."""
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(column)
    elif op.get_context().dialect.name == "duckdb" and not column.nullable:
        with _without_duckdb_indexes(table):
            column.nullable = True
            op.add_column(table, column)
            op.alter_column(table, column.name, nullable=False, existing_type=column.type)
    else:
        op.add_column(table, column)


def _drop_column(table: str, name: str) -> None:
    """Drop the requested column without replacing unrelated table contents."""
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(name)
    elif op.get_context().dialect.name == "duckdb":
        with _without_duckdb_indexes(table):
            op.drop_column(table, name)
    else:
        op.drop_column(table, name)


@contextmanager
def _without_duckdb_indexes(table: str) -> Iterator[None]:
    """Restore exact explicit indexes around one transactional column alteration."""
    connection = op.get_bind()
    indexes = connection.execute(
        sa.text(
            "SELECT schema_name, index_name, sql FROM duckdb_indexes() "
            "WHERE database_name = current_database() "
            "AND schema_name = current_schema() AND table_name = :table_name "
            "ORDER BY index_oid"
        ),
        {"table_name": table},
    ).all()
    if not connection.in_transaction():
        raise RuntimeError("DuckDB column migration requires a transaction")
    for schema, name, _ in indexes:
        op.drop_index(name, table_name=table, schema=schema)
    yield
    for _, _, statement in indexes:
        connection.exec_driver_sql(statement)
