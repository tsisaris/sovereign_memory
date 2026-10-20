"""Map named pipelines and the tasks that belong to them to the relational store."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, relationship

from cognee.infrastructure.databases.relational import Base

from .PipelineTask import PipelineTask
from .Task import Task


class Pipeline(Base):
    """Store a pipeline's identity, description, timestamps and its member tasks."""

    __tablename__ = "pipelines"

    id = Column(Uuid, primary_key=True, default=uuid4)

    name = Column(String)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    tasks: Mapped[list[Task]] = relationship(
        secondary=PipelineTask.__tablename__,
        back_populates="datasets",
    )
