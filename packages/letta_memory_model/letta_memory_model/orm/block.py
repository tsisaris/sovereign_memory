"""Mapped table letta.block: one core-memory block of an organization.

Licensed under Apache-2.0 from the Letta archive (letta/orm/block.py).
"""

from typing import ClassVar, Optional

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from letta_memory_model.orm.base import Base, CommonSqlalchemyMetaMixins
from letta_memory_model.orm.mixins import OrganizationMixin
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase
from letta_memory_model.schemas.block import Block as PydanticBlock
from letta_memory_model.schemas.block import Human, Persona


class Block(SqlalchemyBase, Base, CommonSqlalchemyMetaMixins, OrganizationMixin):
    """A stored block: label, value, character limit and edit permissions.

    version counts changes. The store changes a block only with a
    compare-and-set on version, because this dialect cannot report updated row
    counts and the ORM's own version check would otherwise pass silently.
    """

    __tablename__ = "block"
    __pydantic_model__ = PydanticBlock

    id: Mapped[str] = mapped_column(String, primary_key=True)
    template_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    label: Mapped[str] = mapped_column(String)
    is_template: Mapped[bool] = mapped_column(default=False)
    preserve_on_migration: Mapped[Optional[bool]] = mapped_column(default=False)
    value: Mapped[str] = mapped_column(String)
    limit: Mapped[int] = mapped_column(Integer, default=100000)
    metadata_: Mapped[Optional[dict]] = mapped_column(
        JSON, default=dict, doc="arbitrary information related to the block."
    )

    read_only: Mapped[bool] = mapped_column(default=False)
    hidden: Mapped[Optional[bool]] = mapped_column(nullable=True)
    current_history_entry_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {"version_id_col": version}

    def to_pydantic(self) -> PydanticBlock:
        """Return the row as Human, Persona or Block by its label; raise if invalid."""
        schema: type[PydanticBlock]
        match self.label:
            case "human" | "system/human":
                schema = Human
            case "persona" | "system/persona":
                schema = Persona
            case _:
                schema = PydanticBlock
        model_dict = {k: v for k, v in self.__dict__.items() if k in schema.model_fields}
        model_dict["metadata"] = self.metadata_
        model_dict["created_by_id"] = self._created_by_id
        model_dict["last_updated_by_id"] = self._last_updated_by_id
        return schema.model_validate(model_dict)
