from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


class RoleType(Base):
    __tablename__ = "role_type"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    key: Mapped[str]
    name: Mapped[str]
    permissions: Mapped[list[str]]
    builtin: Mapped[bool] = mapped_column(default=True)


class Project(Base):
    __tablename__ = "project"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(default=DEFAULT_ORG_ID)
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="active")
    delete_reason: Mapped[str | None]
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class RoleName(Base):
    __tablename__ = "role_name"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    name: Mapped[str]
    role_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role_type.id"))
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class Membership(Base):
    __tablename__ = "membership"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    user_id: Mapped[uuid.UUID]
    role_name_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role_name.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    name: Mapped[str]
    prefix: Mapped[str]
    hash: Mapped[bytes]
    scopes: Mapped[list[str]]
    expires_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
