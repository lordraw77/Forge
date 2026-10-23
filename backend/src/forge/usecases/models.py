from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, FetchedValue, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class Catalog(Base):
    __tablename__ = "catalog"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="active")
    builtin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class Category(Base):
    __tablename__ = "category"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog.id"))
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class CatalogAccess(Base):
    __tablename__ = "catalog_access"
    project_id: Mapped[uuid.UUID]
    role_name_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    catalog_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class CategoryExclusion(Base):
    __tablename__ = "category_exclusion"
    project_id: Mapped[uuid.UUID]
    role_name_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    category_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class UseCase(Base):
    __tablename__ = "use_case"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    requirements: Mapped[str] = mapped_column(default="")
    icon: Mapped[str] = mapped_column(default="")
    tags: Mapped[list[str]] = mapped_column(default=list)
    catalog_id: Mapped[uuid.UUID | None]
    category_id: Mapped[uuid.UUID | None]
    pattern: Mapped[str]
    status: Mapped[str] = mapped_column(default="draft")
    input_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    output_type: Mapped[str] = mapped_column(default="markdown")
    output_schema: Mapped[dict[str, Any] | None]
    defaults: Mapped[dict[str, Any]] = mapped_column(default=dict)
    definition: Mapped[dict[str, Any]] = mapped_column(default=dict)
    latest_version: Mapped[int] = mapped_column(default=0)
    published_version: Mapped[int | None]
    dirty: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class UseCaseVersion(Base):
    __tablename__ = "use_case_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    use_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case.id"))
    version: Mapped[int]
    name: Mapped[str]
    description: Mapped[str]
    pattern: Mapped[str]
    input_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    input_schema: Mapped[dict[str, Any]]
    output_type: Mapped[str]
    output_schema: Mapped[dict[str, Any] | None]
    defaults: Mapped[dict[str, Any]]
    definition: Mapped[dict[str, Any]]
    note: Mapped[str] = mapped_column(default="")
    published_at: Mapped[datetime] = mapped_column(default=utcnow)
    published_by: Mapped[uuid.UUID | None]


class UseCaseRun(Base):
    __tablename__ = "use_case_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    use_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case.id"))
    use_case_name: Mapped[str]
    version: Mapped[int | None]
    mode: Mapped[str]
    graph: Mapped[dict[str, Any]]
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    outputs: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(default="queued")
    error_code: Mapped[str | None]
    error: Mapped[str | None]
    error_node: Mapped[str | None]
    limits: Mapped[dict[str, Any]] = mapped_column(default=dict)
    steps: Mapped[int] = mapped_column(default=0)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    trace_id: Mapped[str | None]
    workflow_id: Mapped[str | None]
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("use_case_session.id"))
    webhook_url: Mapped[str | None]
    webhook_status: Mapped[str | None]
    webhook_error: Mapped[str | None]


class UseCaseRunStep(Base):
    __tablename__ = "use_case_run_step"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case_run.id"), primary_key=True)
    step_key: Mapped[str] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    ordinal: Mapped[int] = mapped_column(
        BigInteger, server_default=FetchedValue(), insert_default=None
    )
    node_id: Mapped[str]
    node_type: Mapped[str]
    ref: Mapped[str | None]
    status: Mapped[str]
    input: Mapped[Any | None] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    error_code: Mapped[str | None]
    error: Mapped[str | None]
    agent_run_id: Mapped[uuid.UUID | None]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class UseCaseSession(Base):
    __tablename__ = "use_case_session"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    use_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case.id"))
    title: Mapped[str] = mapped_column(default="")
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class UseCaseFavorite(Base):
    __tablename__ = "use_case_favorite"
    project_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    use_case_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class UseCaseFile(Base):
    __tablename__ = "use_case_file"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    kind: Mapped[str]
    use_case_id: Mapped[uuid.UUID | None]
    run_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    content_type: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str]
    storage_key: Mapped[str]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case_run.id"))
    step_key: Mapped[str]
    node_id: Mapped[str]
    kind: Mapped[str]
    instructions: Mapped[str] = mapped_column(default="")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    assignees: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(default="pending")
    requested_by: Mapped[uuid.UUID | None]
    requested_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    notified_at: Mapped[datetime | None]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    decided_via: Mapped[str | None]
    edited: Mapped[Any | None] = mapped_column(JSONB)
    comment: Mapped[str | None]


class ApprovalToken(Base):
    __tablename__ = "approval_token"
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval.id"))
    user_id: Mapped[uuid.UUID]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class UseCaseTrigger(Base):
    """A schedule (cron or interval) or an inbound token that starts runs of a use case (E3.9)."""

    __tablename__ = "use_case_trigger"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    use_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case.id"))
    name: Mapped[str]
    kind: Mapped[str]
    cron: Mapped[str | None]
    interval_minutes: Mapped[int | None]
    timezone: Mapped[str] = mapped_column(default="UTC")
    token_hash: Mapped[bytes | None]
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    webhook_url: Mapped[str | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None]
    last_run_at: Mapped[datetime | None]
    last_run_id: Mapped[uuid.UUID | None]
    last_status: Mapped[str | None]
    last_error: Mapped[str | None]
    failures: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
