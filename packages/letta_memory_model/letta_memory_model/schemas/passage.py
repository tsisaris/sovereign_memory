"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from typing import Dict, List, Optional

from pydantic import ConfigDict, Field


class Passage:
    """Class docstring."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    embedding_config: Optional[Dict] = None
    metadata_: Dict = Field(default_factory=dict)
    tags: Optional[List[str]] = None
