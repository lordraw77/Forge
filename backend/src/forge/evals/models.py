from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class EvalDataset(Base):
    __tablename__ = "eval_dataset"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    kind: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class EvalCase(Base):
    __tablename__ = "eval_case"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    dataset_id: Mapped[uuid.UUID]
    position: Mapped[int]
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected: Mapped[str | None]
    expected_sources: Mapped[list[str]] = mapped_column(JSONB, default=list)


class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    dataset_id: Mapped[uuid.UUID | None]
    dataset_name: Mapped[str]
    kind: Mapped[str]
    name: Mapped[str] = mapped_column(default="")
    target_id: Mapped[uuid.UUID]
    target_name: Mapped[str]
    target_version: Mapped[int | None]
    model: Mapped[str | None]
    evaluators: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="queued")
    total: Mapped[int] = mapped_column(default=0)
    completed: Mapped[int] = mapped_column(default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    heartbeat_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class EvalResult(Base):
    __tablename__ = "eval_result"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    run_id: Mapped[uuid.UUID]
    case_id: Mapped[uuid.UUID | None]
    position: Mapped[int]
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected: Mapped[str | None]
    output: Mapped[str | None]
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    passed: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None]
    tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(default=0)
    source_run: Mapped[uuid.UUID | None]
