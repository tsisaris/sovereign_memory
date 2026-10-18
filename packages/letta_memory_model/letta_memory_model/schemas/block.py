"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from typing import Dict, Optional

from pydantic import ConfigDict, Field


class Block:
    """Class docstring."""

    model_config = ConfigDict(from_attributes=True)
    id: Optional[str] = None
    label: str
    value: str
    limit: int = 100000
    description: Optional[str] = None
    is_template: bool = False
    read_only: bool = False
    metadata: Dict = Field(default_factory=dict)


class Human(Block):
    """Human."""

    label: str = "human"


class Persona(Block):
    """Persona."""

    label: str = "persona"
