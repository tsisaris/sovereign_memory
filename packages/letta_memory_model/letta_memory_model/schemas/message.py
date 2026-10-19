"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from typing import Dict, List, Optional

from pydantic import ConfigDict


class Message:
    """Class docstring."""

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
    id: str
    role: str
    text: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    step_id: Optional[str] = None
    run_id: Optional[str] = None
    otid: Optional[str] = None
    group_id: Optional[str] = None
    sender_id: Optional[str] = None
    batch_item_id: Optional[str] = None
    conversation_id: Optional[str] = None
    is_err: Optional[bool] = None
    approval_request_id: Optional[str] = None
    approve: Optional[bool] = None
    denial_reason: Optional[str] = None
    sequence_id: int
