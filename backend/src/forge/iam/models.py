from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(default=DEFAULT_ORG_ID)
    username: Mapped[str]
    email: Mapped[str]
    full_name: Mapped[str] = mapped_column(default="")
    auth_source: Mapped[str] = mapped_column(default="local")
    provider_id: Mapped[uuid.UUID | None]
    external_id: Mapped[str | None]
    external_groups: Mapped[list[str]] = mapped_column(default=list)
    password_hash: Mapped[str | None]
    is_super_admin: Mapped[bool] = mapped_column(default=False)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    totp_secret_id: Mapped[uuid.UUID | None]
    totp_enabled: Mapped[bool] = mapped_column(default=False)
    totp_last_step: Mapped[int] = mapped_column(default=0)
    failed_logins: Mapped[int] = mapped_column(default=0)
    first_failed_at: Mapped[datetime | None]
    locked_until: Mapped[datetime | None]
    eula_version: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="active")
    last_active_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class PasswordHistory(Base):
    __tablename__ = "password_history"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RecoveryCode(Base):
    __tablename__ = "recovery_code"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    code_hash: Mapped[bytes]
    used_at: Mapped[datetime | None]


class AuthSession(Base):
    __tablename__ = "auth_session"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    family_id: Mapped[uuid.UUID]
    refresh_hash: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(default=utcnow)
    idle_expires_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    rotated_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    ip: Mapped[str | None]
    user_agent: Mapped[str | None]


class IdentityProvider(Base):
    __tablename__ = "identity_provider"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str]
    name: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secret_id: Mapped[uuid.UUID | None]
    last_sync_at: Mapped[datetime | None]
    last_sync: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
