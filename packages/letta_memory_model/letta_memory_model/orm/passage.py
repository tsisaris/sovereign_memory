"""Mapped tables letta.source_passages and letta.archival_passages.

Licensed under Apache-2.0 from the Letta archive (letta/orm/passage.py).
Embeddings are native DuckDB FLOAT[] lists, compared with list_cosine_distance.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ARRAY, JSON, Float, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from letta_memory_model.orm.base import Base, CommonSqlalchemyMetaMixins
from letta_memory_model.orm.mixins import ArchiveMixin, FileMixin, OrganizationMixin, SourceMixin
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase
from letta_memory_model.schemas.passage import Passage as PydanticPassage

if TYPE_CHECKING:
    from letta_memory_model.orm.passage_tag import PassageTag


class BasePassage:
    """Columns shared by both passage tables: text, metadata, tags and embedding.

    embedding holds the vector unpadded; its length is the embedding's
    dimension, so a search only compares vectors of the query's dimension.
    """

    __abstract__ = True
    __pydantic_model__ = PydanticPassage

    id: Mapped[str] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column()
    embedding_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column(JSON)
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(ARRAY(Float), nullable=True)


class SourcePassage(
    BasePassage,
    Base,
    FileMixin,
    SourceMixin,
    OrganizationMixin,
    CommonSqlalchemyMetaMixins,
    SqlalchemyBase,
):
    """A passage cut from a source file, deleted with its source or file."""

    __tablename__ = "source_passages"
    __table_args__ = (Index("ix_source_passages_org_source", "organization_id", "source_id"),)
    file_name: Mapped[str] = mapped_column()


class ArchivalPassage(
    BasePassage, Base, ArchiveMixin, OrganizationMixin, CommonSqlalchemyMetaMixins, SqlalchemyBase
):
    """A passage of an agent archive; its tags are mirrored in letta.passage_tags."""

    __tablename__ = "archival_passages"
    __table_args__ = (Index("ix_archival_passages_org_archive", "organization_id", "archive_id"),)
    passage_tags: Mapped[List["PassageTag"]] = relationship(
        "letta_memory_model.orm.passage_tag.PassageTag",
        primaryjoin="ArchivalPassage.id == foreign(PassageTag.passage_id)",
        back_populates="passage",
        cascade="all, delete-orphan",
        lazy="noload",
    )
