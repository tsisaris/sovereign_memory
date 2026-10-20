"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from datetime import datetime
from typing import Dict, Optional

from pydantic import ConfigDict


class Conversation:
    """Class docstring."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_id: str
    summary: Optional[str] = None
    model: Optional[str] = None
    model_settings: Optional[Dict] = None
    last_message_at: Optional[datetime] = None
