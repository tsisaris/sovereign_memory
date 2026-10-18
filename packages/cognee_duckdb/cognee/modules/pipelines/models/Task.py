from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, relationship

from cognee.infrastructure.databases.relational import Base

from .PipelineTask import PipelineTask


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Uuid, primary_key=True, default=uuid4)

    name = Column(String)
    description = Column(Text, nullable=True)

    executable = Column(Text)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    datasets: Mapped[list["Pipeline"]] = relationship(
        secondary=PipelineTask.__tablename__, back_populates="task"
    )
