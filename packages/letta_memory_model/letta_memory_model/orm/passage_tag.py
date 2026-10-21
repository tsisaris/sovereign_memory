"""Mapped table letta.passage_tags: one tag row per tag of an archival passage.

Licensed under Apache-2.0 from the Letta archive (letta/orm/passage_tag.py).
"""

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from letta_memory_model.orm.base import Base
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase

if TYPE_CHECKING:
    from letta_memory_model.orm.passage import ArchivalPassage


class PassageTag(SqlalchemyBase, Base):
    """One tag of one archival passage, kept for tag filtering.

    passage_id names a letta.archival_passages row. Upstream declares that key
    with ON DELETE CASCADE, which DuckDB rejects; the store deletes a passage's
    tags in the same transaction that deletes the passage.
    """

    __tablename__ = "passage_tags"
    __table_args__ = (
        UniqueConstraint("passage_id", "tag", name="uq_passage_tag"),
        Index("ix_passage_tags_archive_tag", "archive_id", "tag"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, nullable=False)
    passage_id: Mapped[str] = mapped_column(String, nullable=False)
    archive_id: Mapped[str] = mapped_column(String, nullable=False)
    organization_id: Mapped[str] = mapped_column(String, nullable=False)
    passage: Mapped["ArchivalPassage"] = relationship(
        "letta_memory_model.orm.passage.ArchivalPassage",
        primaryjoin="foreign(PassageTag.passage_id) == ArchivalPassage.id",
        back_populates="passage_tags",
        lazy="noload",
    )
