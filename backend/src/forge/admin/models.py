from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class Secret(Base):
    __tablename__ = "secret"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID | None]
    kind: Mapped[str]
    kek_id: Mapped[str]
    wrapped_dek: Mapped[bytes]
    nonce: Mapped[bytes]
    ciphertext: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    rotated_at: Mapped[datetime | None]


class Setting(Base):
    __tablename__ = "setting"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    key: Mapped[str]
    project_id: Mapped[uuid.UUID | None]
    value: Mapped[dict[str, Any]]
    version: Mapped[int]
    updated_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class SettingHistory(Base):
    __tablename__ = "setting_history"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    key: Mapped[str]
    project_id: Mapped[uuid.UUID | None]
    value: Mapped[dict[str, Any]]
    version: Mapped[int]
    updated_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID | None]
    actor_id: Mapped[uuid.UUID | None]
    actor_label: Mapped[str]
    action: Mapped[str]
    target_type: Mapped[str]
    target_id: Mapped[str | None]
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ip: Mapped[str | None]
    user_agent: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
