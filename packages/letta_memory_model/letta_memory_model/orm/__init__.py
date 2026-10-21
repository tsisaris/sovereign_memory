"""Letta's mapped tables, all registered on one metadata in the reserved letta schema.

Importing this package imports every mapped module, so SQLAlchemy can resolve
relationships between them in one registry.
"""

from letta_memory_model.orm.base import LETTA_SCHEMA, Base, CommonSqlalchemyMetaMixins
from letta_memory_model.orm.block import Block
from letta_memory_model.orm.blocks_agents import BlocksAgents
from letta_memory_model.orm.conversation import Conversation
from letta_memory_model.orm.message import Message
from letta_memory_model.orm.mixins import (
    AgentMixin,
    ArchiveMixin,
    FileMixin,
    OrganizationMixin,
    ProjectMixin,
    SourceMixin,
    TemplateEntityMixin,
    TemplateMixin,
)
from letta_memory_model.orm.passage import ArchivalPassage, SourcePassage
from letta_memory_model.orm.passage_tag import PassageTag
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase

__all__ = [
    "LETTA_SCHEMA",
    "AgentMixin",
    "ArchivalPassage",
    "ArchiveMixin",
    "Base",
    "Block",
    "BlocksAgents",
    "CommonSqlalchemyMetaMixins",
    "Conversation",
    "FileMixin",
    "Message",
    "OrganizationMixin",
    "PassageTag",
    "ProjectMixin",
    "SourceMixin",
    "SourcePassage",
    "SqlalchemyBase",
    "TemplateEntityMixin",
    "TemplateMixin",
]
