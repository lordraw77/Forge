from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class Prompt(Base):
    __tablename__ = "prompt"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    latest_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class PromptVersion(Base):
    __tablename__ = "prompt_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    prompt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prompt.id"))
    version: Mapped[int]
    model: Mapped[str]
    system_template: Mapped[str] = mapped_column(default="")
    user_template: Mapped[str] = mapped_column(default="")
    variables: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    model_params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class PromptTag(Base):
    __tablename__ = "prompt_tag"
    prompt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prompt.id"), primary_key=True)
    project_id: Mapped[uuid.UUID]
    tag: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class PromptRun(Base):
    __tablename__ = "prompt_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    prompt_id: Mapped[uuid.UUID | None]
    version: Mapped[int | None]
    kind: Mapped[str]
    group_id: Mapped[uuid.UUID | None]
    model: Mapped[str]
    model_name: Mapped[str | None]
    status: Mapped[str]
    error_code: Mapped[str | None]
    error: Mapped[str | None]
    variables: Mapped[dict[str, Any]] = mapped_column(default=dict)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[str | None]
    finish_reason: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    latency_ms: Mapped[int]
    cache_hit: Mapped[bool] = mapped_column(default=False)
    llm_call_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
