"""Map tenant principals, owners and their user and role relationships."""

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, relationship

from .Principal import Principal
from .Role import Role
from .UserTenant import UserTenant

if TYPE_CHECKING:
    from .User import User


class Tenant(Principal):
    """Keep tenant identity and the users and roles belonging to that tenant."""

    __tablename__ = "tenants"

    id = Column(Uuid, ForeignKey("principals.id"), primary_key=True)
    name = Column(String, unique=False, nullable=False, index=True)

    owner_id = Column(Uuid, index=True)

    users: Mapped[list["User"]] = relationship(
        "User",
        secondary=UserTenant.__tablename__,
        back_populates="tenants",
    )

    # One-to-Many relationship with Role
    roles = relationship(
        "Role",
        back_populates="tenant",
        foreign_keys=lambda: [Role.tenant_id],
    )

    __mapper_args__: ClassVar[dict[str, str]] = {
        "polymorphic_identity": "tenant",
    }
