"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


class OrganizationMixin:
    """Class docstring."""

    __abstract__ = True
    organization_id: Mapped[str] = mapped_column(String, nullable=True)


class AgentMixin:
    """Class docstring."""

    __abstract__ = True
    agent_id: Mapped[str] = mapped_column(String, nullable=True)


class FileMixin:
    """Class docstring."""

    __abstract__ = True
    file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class SourceMixin:
    """Class docstring."""

    __abstract__ = True
    source_id: Mapped[str] = mapped_column(String, nullable=True)


class ArchiveMixin:
    """Class docstring."""

    __abstract__ = True
    archive_id: Mapped[str] = mapped_column(String, nullable=True)


class ProjectMixin:
    """Class docstring."""

    __abstract__ = True
    project_id: Mapped[str] = mapped_column(String, nullable=True)


class TemplateMixin:
    """Class docstring."""

    __abstract__ = True
    base_template_id: Mapped[str] = mapped_column(nullable=True)
    template_id: Mapped[str] = mapped_column(nullable=True)
    deployment_id: Mapped[str] = mapped_column(nullable=True)


class TemplateEntityMixin:
    """Class docstring."""

    __abstract__ = True
    entity_id: Mapped[str] = mapped_column(nullable=True)
