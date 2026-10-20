"""Map the link table that says which tasks belong to which pipeline."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Uuid

from cognee.infrastructure.databases.relational import Base


class PipelineTask(Base):
    """Store one pipeline-to-task membership; the two ids together are its key."""

    __tablename__ = "pipeline_task"

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    pipeline_id = Column("pipeline", Uuid, ForeignKey("pipelines.id"), primary_key=True)
    task_id = Column("task", Uuid, ForeignKey("tasks.id"), primary_key=True)
