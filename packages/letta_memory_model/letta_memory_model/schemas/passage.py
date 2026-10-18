"""Pydantic passage schema: one archival or source passage with its embedding.

Licensed under Apache-2.0 from the Letta archive (letta/schemas/passage.py).
"""

from typing import Dict, List, Optional

from pydantic import Field

from letta_memory_model.schemas.letta_base import OrmMetadataBase


class PassageBase(OrmMetadataBase):
    """The descriptive fields of a passage: where it belongs and its metadata.

    metadata is read from the stored column metadata_. It never reads or
    writes storage.
    """

    __id_prefix__ = "passage"

    is_deleted: bool = Field(default=False, description="Whether this passage is deleted.")
    archive_id: Optional[str] = Field(
        default=None, description="The archive of an archival passage."
    )
    source_id: Optional[str] = Field(default=None, description="The source of a source passage.")
    file_id: Optional[str] = Field(default=None, description="The file of a source passage.")
    file_name: Optional[str] = Field(default=None, description="The name of that file.")
    metadata: Optional[Dict] = Field(
        default_factory=dict, validation_alias="metadata_", description="Passage metadata."
    )
    tags: Optional[List[str]] = Field(default=None, description="Tags attached to the passage.")


class Passage(PassageBase):
    """A stored passage: its text, optional embedding vector and identity."""

    id: str = PassageBase.generate_id_field()
    text: str = Field(..., description="The text of the passage.")
    embedding: Optional[List[float]] = Field(default=None, description="The embedding vector.")
    embedding_config: Optional[Dict] = Field(default=None, description="How it was embedded.")
