"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


class BlocksAgents:
    """Class docstring."""

    __tablename__ = "blocks_agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    block_id: Mapped[str] = mapped_column(String, primary_key=True)
    block_label: Mapped[str] = mapped_column(String, primary_key=True)
