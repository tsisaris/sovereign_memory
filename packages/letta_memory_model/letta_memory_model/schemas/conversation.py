"""Pydantic conversation schema: one conversation thread of an agent.

Licensed under Apache-2.0 from the Letta archive (letta/schemas/conversation.py).
"""

from datetime import datetime
from typing import Dict, Optional

from pydantic import Field

from letta_memory_model.schemas.letta_base import OrmMetadataBase


class ConversationBase(OrmMetadataBase):
    """The identity prefix shared by conversation schemas; it holds no fields."""

    __id_prefix__ = "conv"


class Conversation(ConversationBase):
    """A conversation thread that groups an agent's messages.

    It never reads or writes storage; deleting it keeps its messages and only
    clears their conversation_id, as the original foreign-key rule did.
    """

    id: str = ConversationBase.generate_id_field()
    agent_id: str = Field(..., description="The agent that owns the conversation.")
    summary: Optional[str] = None
    model: Optional[str] = None
    model_settings: Optional[Dict] = None
    last_message_at: Optional[datetime] = None
