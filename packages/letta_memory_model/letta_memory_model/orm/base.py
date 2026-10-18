"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declarative_mixin, declared_attr, mapped_column


class Base(DeclarativeBase):
    """Class docstring."""

    pass


@declarative_mixin
class CommonSqlalchemyMetaMixins:
    """Class docstring."""

    __abstract__ = True
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    @declared_attr
    def _created_by_id(cls):
        return mapped_column(String, nullable=True)

    @declared_attr
    def _last_updated_by_id(cls):
        return mapped_column(String, nullable=True)
