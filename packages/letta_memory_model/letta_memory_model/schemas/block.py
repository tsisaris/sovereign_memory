"""Pydantic memory-block schemas: the validated shape of one core-memory block.

Licensed under Apache-2.0 from the Letta archive (letta/schemas/block.py).
"""

from typing import Optional

from pydantic import Field, model_validator

from letta_memory_model.constants import CORE_MEMORY_BLOCK_CHAR_LIMIT
from letta_memory_model.schemas.letta_base import LettaBase


class BaseBlock(LettaBase, validate_assignment=True):
    """The editable fields of a block, re-validated on every assignment.

    It takes a label, a value and its character limit; it never accepts a value
    longer than its limit, on construction or on later assignment. It never
    reads or writes storage.
    """

    __id_prefix__ = "block"

    # Upstream requires value; an empty default keeps the existing no-argument
    # construction used by the package's memory-tool tests valid.
    value: str = Field(default="", description="Value of the block.")
    limit: int = Field(
        default=CORE_MEMORY_BLOCK_CHAR_LIMIT, description="Character limit of the block."
    )
    project_id: Optional[str] = Field(default=None, description="The associated project id.")
    template_name: Optional[str] = Field(default=None, description="Name of the block template.")
    is_template: bool = Field(default=False, description="Whether the block is a template.")
    template_id: Optional[str] = Field(default=None, description="The id of the template.")
    base_template_id: Optional[str] = Field(default=None, description="The base template id.")
    deployment_id: Optional[str] = Field(default=None, description="The id of the deployment.")
    entity_id: Optional[str] = Field(default=None, description="The id of the entity.")
    preserve_on_migration: Optional[bool] = Field(
        default=False, description="Preserve the block on template migration."
    )
    label: Optional[str] = Field(default=None, description="Label of the block (e.g. 'human').")
    read_only: bool = Field(default=False, description="Whether the agent may edit this block.")
    description: Optional[str] = Field(default=None, description="Description of the block.")
    metadata: Optional[dict] = Field(default_factory=dict, description="Metadata of the block.")
    hidden: Optional[bool] = Field(default=None, description="Whether the block is hidden.")

    @model_validator(mode="after")
    def verify_char_limit(self) -> "BaseBlock":
        """Refuse a value longer than the limit; return the block unchanged otherwise."""
        if self.value and len(self.value) > self.limit:
            raise ValueError(
                f"Exceeds {self.limit} character limit (requested {len(self.value)}) "
                f"for block {self.label}."
            )
        return self


class Block(BaseBlock):
    """A stored block: the editable fields plus its identity and audit fields.

    version is the stored optimistic-concurrency counter; a caller passes it
    back when changing the block so a concurrent change is refused, not lost.
    """

    id: str = BaseBlock.generate_id_field()
    version: int = Field(default=1, description="Stored version, incremented on every change.")
    created_by_id: Optional[str] = Field(default=None, description="The id of the creating user.")
    last_updated_by_id: Optional[str] = Field(
        default=None, description="The id of the last editor."
    )


class Human(Block):
    """A block whose default label is "human"."""

    label: str = "human"


class Persona(Block):
    """A block whose default label is "persona"."""

    label: str = "persona"


class FileBlock(Block):
    """A block that shows an attached file's content in the agent's context."""

    file_id: str
    source_id: str
    is_open: bool = False
