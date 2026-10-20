from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class GuardrailPolicy(Base):
    __tablename__ = "guardrail_policy"
    project_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class GuardrailEvent(Base):
    __tablename__ = "guardrail_event"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    stage: Mapped[str]
    source: Mapped[str]
    rule_index: Mapped[int]
    check_type: Mapped[str]
    action: Mapped[str]
    label: Mapped[str] = mapped_column(default="")
    detail: Mapped[str] = mapped_column(default="")
    matches: Mapped[int] = mapped_column(default=0)
    user_id: Mapped[uuid.UUID | None]
    api_key_id: Mapped[uuid.UUID | None]
    trace_id: Mapped[str | None]
