"""Pydantic recall-message schema: one stored message of an agent's history.

Licensed under Apache-2.0 from the Letta archive (letta/schemas/message.py),
reduced to the columns this package stores.
"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import Field

from letta_memory_model.schemas.letta_base import LettaBase


class BaseMessage(LettaBase):
    """The identity prefix shared by message schemas; it holds no fields."""

    __id_prefix__ = "message"


class Message(BaseMessage):
    """One message of an agent's recall history, as validated data.

    It carries the message's role, text or content parts, tool traffic and its
    position (sequence_id, assigned by storage). It never reads or writes storage.
    """

    id: str = BaseMessage.generate_id_field()
    agent_id: Optional[str] = Field(default=None, description="The agent that owns the message.")
    role: str = Field(..., description="The role of the participant.")
    text: Optional[str] = Field(default=None, description="The text of the message.")
    content: Optional[List[Dict]] = Field(default=None, description="The content parts.")
    model: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    step_id: Optional[str] = None
    run_id: Optional[str] = None
    otid: Optional[str] = None
    tool_returns: Optional[List[Dict]] = None
    group_id: Optional[str] = None
    sender_id: Optional[str] = None
    batch_item_id: Optional[str] = None
    conversation_id: Optional[str] = None
    is_err: Optional[bool] = None
    approval_request_id: Optional[str] = None
    approve: Optional[bool] = None
    denial_reason: Optional[str] = None
    approvals: Optional[List[Dict]] = None
    sequence_id: Optional[int] = Field(default=None, description="Storage-assigned position.")
    created_at: Optional[datetime] = None
