"""Declarative base and shared audit columns for Letta's tables in DuckDB.

Licensed under Apache-2.0 from the Letta archive (letta/orm/base.py). Every
table, sequence and foreign-key target of this package lives in the reserved
DuckDB schema named by LETTA_SCHEMA, so Letta never shares a table with another
engine (Mem0's main.messages has the same name and different columns).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DDL, Boolean, DateTime, MetaData, String, event, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declarative_mixin, declared_attr, mapped_column

LETTA_SCHEMA = "letta"


class Base(DeclarativeBase):
    """The declarative registry of every Letta table, all qualified to LETTA_SCHEMA.

    It never changes a connection's default schema; each statement names the
    schema itself.
    """

    metadata = MetaData(schema=LETTA_SCHEMA)


# Plain metadata.create_all() first creates the reserved schema if it is missing.
# letta_duckdb_schema.ensure_schema is the checked path: it also refuses existing
# tables of another shape, which create_all(checkfirst=True) would silently keep.
event.listen(Base.metadata, "before_create", DDL(f"CREATE SCHEMA IF NOT EXISTS {LETTA_SCHEMA}"))


@declarative_mixin
class CommonSqlalchemyMetaMixins:
    """Audit columns shared by Letta's tables: timestamps, soft-delete and editors.

    updated_at is set explicitly by the store on every change; DuckDB has no
    automatic update trigger.
    """

    __abstract__ = True
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    @declared_attr
    def _created_by_id(cls) -> Mapped[Optional[str]]:
        """Return the column naming the user who created the row."""
        return mapped_column(String, nullable=True)

    @declared_attr
    def _last_updated_by_id(cls) -> Mapped[Optional[str]]:
        """Return the column naming the user who last changed the row."""
        return mapped_column(String, nullable=True)
