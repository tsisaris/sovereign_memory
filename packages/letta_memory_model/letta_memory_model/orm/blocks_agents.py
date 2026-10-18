"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from letta_memory_model.orm.base import Base
from letta_memory_model.orm.sqlalchemy_base import SqlalchemyBase


class BlocksAgents(SqlalchemyBase, Base):
    """Class docstring."""

    __tablename__ = "blocks_agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    block_id: Mapped[str] = mapped_column(String, primary_key=True)
    block_label: Mapped[str] = mapped_column(String, primary_key=True)
