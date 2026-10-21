"""Mapped table letta.blocks_agents: which blocks are attached to which agent.

Licensed under Apache-2.0 from the Letta archive (letta/orm/blocks_agents.py).
"""

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from letta_memory_model.orm.base import Base
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase


class BlocksAgents(SqlalchemyBase, Base):
    """One attachment of a block to an agent, carrying the block's current label.

    An agent holds at most one block per label and each block once. The
    upstream foreign key to letta.block is enforced by the store instead of a
    DuckDB constraint: DuckDB refuses to delete a referenced row even after its
    references were removed in the same transaction, so a declared key would
    make atomic detach-and-delete impossible.
    """

    __tablename__ = "blocks_agents"
    __table_args__ = (
        UniqueConstraint("agent_id", "block_label", name="unique_label_per_agent"),
        UniqueConstraint("agent_id", "block_id", name="unique_agent_block"),
        Index("ix_blocks_agents_block_label_agent_id", "block_label", "agent_id"),
    )

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    block_id: Mapped[str] = mapped_column(String, primary_key=True)
    block_label: Mapped[str] = mapped_column(String, primary_key=True)
