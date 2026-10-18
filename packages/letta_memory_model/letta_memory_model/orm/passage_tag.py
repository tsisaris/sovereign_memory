"""Module docstring."""

from typing import TYPE_CHECKING

# Licensed under Apache-2.0 from Letta archive
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from letta_memory_model.orm.base import Base
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase

if TYPE_CHECKING:
    from letta_memory_model.orm.passage import ArchivalPassage


class PassageTag(SqlalchemyBase, Base):
    """Class docstring."""

    __tablename__ = "passage_tags"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, nullable=False)
    passage_id: Mapped[str] = mapped_column(
        String, ForeignKey("archival_passages.id", ondelete="CASCADE"), nullable=False
    )
    archive_id: Mapped[str] = mapped_column(String, nullable=False)
    passage: Mapped["ArchivalPassage"] = relationship(
        "letta_memory_model.orm.passage.ArchivalPassage",
        back_populates="passage_tags",
        lazy="noload",
    )
