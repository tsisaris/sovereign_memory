"""Mapped table letta.conversations: an agent's conversation threads.

Licensed under Apache-2.0 from the Letta archive (letta/orm/conversation.py).
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from letta_memory_model.orm.base import Base, CommonSqlalchemyMetaMixins
from letta_memory_model.orm.mixins import OrganizationMixin
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase
from letta_memory_model.schemas.conversation import Conversation as PydanticConversation


class Conversation(SqlalchemyBase, Base, OrganizationMixin, CommonSqlalchemyMetaMixins):
    """A conversation thread; deleting it clears conversation_id on its messages."""

    __tablename__ = "conversations"
    __pydantic_model__ = PydanticConversation

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: f"conv-{uuid.uuid4()}"
    )
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
