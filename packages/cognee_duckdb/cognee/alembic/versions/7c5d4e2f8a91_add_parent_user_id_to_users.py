"""Add parent_user_id to users.

Revision ID: 7c5d4e2f8a91
Revises: b2c3d4e5f6a7
Create Date: 2026-04-23 21:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c5d4e2f8a91"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "users"
COLUMN_NAME = "parent_user_id"
FK_NAME = "fk_users_parent_user_id_users"


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    """Check actual table metadata before adding an existing column."""
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _foreign_key_name(inspector: sa.Inspector, table_name: str, fk_name: str) -> str | None:
    """Recognize the named constraint or DuckDB's identical native relationship."""
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("name") == fk_name or (
            inspector.bind.dialect.name == "duckdb"
            and foreign_key.get("constrained_columns") == [COLUMN_NAME]
            and foreign_key.get("referred_table") == TABLE_NAME
            and foreign_key.get("referred_columns") == ["id"]
            and foreign_key.get("referred_schema") in (None, inspector.default_schema_name)
        ):
            return foreign_key["name"]
    return None


def upgrade() -> None:
    """Add the parent relationship unless the actual schema already contains it."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if TABLE_NAME not in inspector.get_table_names():
        return

    if not _has_column(inspector, TABLE_NAME, COLUMN_NAME):
        if op.get_context().dialect.name == "sqlite":
            with op.batch_alter_table(TABLE_NAME) as batch_op:
                batch_op.add_column(sa.Column(COLUMN_NAME, sa.UUID(), nullable=True))
        else:
            op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, sa.UUID(), nullable=True))

    if op.get_context().dialect.name != "sqlite":
        inspector = sa.inspect(connection)
        if _foreign_key_name(inspector, TABLE_NAME, FK_NAME) is None:
            op.create_foreign_key(
                FK_NAME,
                TABLE_NAME,
                TABLE_NAME,
                [COLUMN_NAME],
                ["id"],
            )


def downgrade() -> None:
    """Remove the reflected parent constraint and column without inventing names."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if TABLE_NAME not in inspector.get_table_names() or not _has_column(
        inspector, TABLE_NAME, COLUMN_NAME
    ):
        return

    fk_name = _foreign_key_name(inspector, TABLE_NAME, FK_NAME)
    if op.get_context().dialect.name != "sqlite" and fk_name is not None:
        op.drop_constraint(fk_name, TABLE_NAME, type_="foreignkey")

    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_column(COLUMN_NAME)
    else:
        op.drop_column(TABLE_NAME, COLUMN_NAME)
