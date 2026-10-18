"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from typing import ClassVar, Optional

from sqlalchemy import JSON, BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from letta_memory_model.schemas.block import Block as PydanticBlock
from letta_memory_model.schemas.block import Human, Persona


class Block:
    """Class docstring."""

    __tablename__ = "block"
    __pydantic_model__ = PydanticBlock

    id: Mapped[str] = mapped_column(String, primary_key=True)
    template_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    label: Mapped[str] = mapped_column(String)
    is_template: Mapped[bool] = mapped_column(default=False)
    preserve_on_migration: Mapped[Optional[bool]] = mapped_column(default=False)
    value: Mapped[str] = mapped_column(String)
    limit: Mapped[BigInteger] = mapped_column(Integer, default=100000)
    metadata_: Mapped[Optional[dict]] = mapped_column(
        JSON, default={}, doc="arbitrary information related to the block."
    )

    read_only: Mapped[bool] = mapped_column(default=False)
    hidden: Mapped[Optional[bool]] = mapped_column(nullable=True)
    current_history_entry_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {"version_id_col": version}

    def to_pydantic(self):
        """Convert to Pydantic."""
        match self.label:
            case "human" | "system/human":
                Schema = Human
            case "persona" | "system/persona":
                Schema = Persona
            case _:
                Schema = PydanticBlock
        model_dict = {
            k: v for k, v in self.__dict__.items() if k in self.__pydantic_model__.model_fields
        }
        model_dict["metadata"] = self.metadata_
        return Schema.model_validate(model_dict)
