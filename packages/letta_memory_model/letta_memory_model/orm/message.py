"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from typing import List, Optional

from sqlalchemy import JSON, BigInteger, FetchedValue, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from letta_memory_model.schemas.message import Message as PydanticMessage


class Message:
    """Class docstring."""

    __tablename__ = "messages"
    __pydantic_model__ = PydanticMessage

    id: Mapped[str] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column()
    text: Mapped[Optional[str]] = mapped_column(nullable=True)
    content: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(nullable=True)
    name: Mapped[Optional[str]] = mapped_column(nullable=True)
    tool_calls: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    step_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    otid: Mapped[Optional[str]] = mapped_column(nullable=True)
    tool_returns: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)
    group_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    sender_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    batch_item_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    is_err: Mapped[Optional[bool]] = mapped_column(nullable=True)
    approval_request_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    approve: Mapped[Optional[bool]] = mapped_column(nullable=True)
    denial_reason: Mapped[Optional[str]] = mapped_column(nullable=True)
    approvals: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)
    sequence_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, server_default=FetchedValue()
    )
