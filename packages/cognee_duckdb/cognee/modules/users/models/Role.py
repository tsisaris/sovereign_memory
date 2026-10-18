"""Map tenant-specific roles and their user membership."""

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, relationship

from .Principal import Principal
from .UserRole import UserRole

if TYPE_CHECKING:
    from .User import User


class Role(Principal):
    """Store a uniquely named tenant role and its associated principals."""

    __tablename__ = "roles"

    id = Column(Uuid, ForeignKey("principals.id", ondelete="CASCADE"), primary_key=True)

    name = Column(String, nullable=False, index=True)

    users: Mapped[list["User"]] = relationship(
        "User",
        secondary=UserRole.__tablename__,
        back_populates="roles",
    )

    # Foreign key to Tenant (Many-to-One relationship)
    tenant_id = Column(Uuid, ForeignKey("tenants.id"), nullable=False)

    # Relationship to Tenant
    tenant = relationship("Tenant", back_populates="roles", foreign_keys=[tenant_id])

    # Unique constraint on tenant_id and name
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_id_name"),)

    __mapper_args__: ClassVar[dict[str, str]] = {
        "polymorphic_identity": "role",
    }
