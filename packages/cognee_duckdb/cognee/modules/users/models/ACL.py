"""The access-control-list table model: which principal holds which permission on which dataset.

It defines a table and nothing else; it opens no connection.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import relationship

from cognee.infrastructure.databases.relational import Base


class ACL(Base):
    """One row of the acls table: an id, creation and update times, the principal it applies to and the permission and dataset it grants.

    It is a table definition and does nothing by itself.
    """

    __tablename__ = "acls"

    id = Column(Uuid, primary_key=True, default=uuid4)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    principal_id = Column(Uuid, ForeignKey("principals.id"))
    permission_id = Column(Uuid, ForeignKey("permissions.id"))
    dataset_id = Column(Uuid, ForeignKey("datasets.id", ondelete="CASCADE"))

    principal = relationship("Principal")
    permission = relationship("Permission")
    dataset = relationship("Dataset", back_populates="acls")
