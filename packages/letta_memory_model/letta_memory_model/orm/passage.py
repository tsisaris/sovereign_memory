"""Module docstring."""

from typing import TYPE_CHECKING, List, Optional

# Licensed under Apache-2.0 from Letta archive
from sqlalchemy import JSON, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from letta_memory_model.orm.base import Base, CommonSqlalchemyMetaMixins
from letta_memory_model.orm.custom_columns import CommonVector
from letta_memory_model.orm.mixins import ArchiveMixin, OrganizationMixin, SourceMixin
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase
from letta_memory_model.schemas.passage import Passage as PydanticPassage

if TYPE_CHECKING:
    from letta_memory_model.orm.passage_tag import PassageTag


class BasePassage:
    """Class docstring."""

    __abstract__ = True
    __pydantic_model__ = PydanticPassage

    id: Mapped[str] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column()
    embedding_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column(JSON)
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    # Map pgvector to DuckDB FLOAT[n] array. Standard DuckDB SQLAlchemy lacks native array mapped_column currently, using binary blob fallback logic.
    embedding = Column(CommonVector, nullable=True)


class SourcePassage(
    BasePassage, Base, SourceMixin, OrganizationMixin, CommonSqlalchemyMetaMixins, SqlalchemyBase
):
    """Class docstring."""

    __tablename__ = "source_passages"
    file_name: Mapped[str] = mapped_column()


class ArchivalPassage(
    BasePassage, Base, ArchiveMixin, OrganizationMixin, CommonSqlalchemyMetaMixins, SqlalchemyBase
):
    """Class docstring."""

    __tablename__ = "archival_passages"
    passage_tags: Mapped[List["PassageTag"]] = relationship(
        "letta_memory_model.orm.passage_tag.PassageTag",
        back_populates="passage",
        cascade="all, delete-orphan",
        lazy="noload",
    )
