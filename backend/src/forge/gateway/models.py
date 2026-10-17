from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class ModelProvider(Base):
    __tablename__ = "model_provider"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    provider_type: Mapped[str]
    base_url: Mapped[str | None]
    secret_id: Mapped[uuid.UUID | None]
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class Model(Base):
    __tablename__ = "model"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID | None]
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_provider.id"))
    name: Mapped[str]
    display_name: Mapped[str]
    model_type: Mapped[str]
    model_id: Mapped[str]
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    context_window: Mapped[int | None]
    max_output_tokens: Mapped[int | None]
    embedding_dims: Mapped[int | None]
    cost_input: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    cost_output: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    capabilities: Mapped[list[str]] = mapped_column(default=list)
    timeout_s: Mapped[int] = mapped_column(default=60)
    max_retries: Mapped[int] = mapped_column(default=2)
    rpm_limit: Mapped[int | None]
    all_projects: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class ModelEnablement(Base):
    __tablename__ = "model_enablement"
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model.id"), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class ModelAlias(Base):
    __tablename__ = "model_alias"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    model_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class GatewayProjectConfig(Base):
    __tablename__ = "gateway_project_config"
    project_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tokens_per_day: Mapped[int | None]
    requests_per_minute: Mapped[int | None]
    user_tokens_per_day: Mapped[int | None]
    user_requests_per_minute: Mapped[int | None]
    max_tokens_per_call: Mapped[int | None]
    max_tokens_policy: Mapped[str | None]
    cache_enabled: Mapped[bool] = mapped_column(default=False)
    cache_ttl_s: Mapped[int] = mapped_column(default=3600)
    cache_nonzero_temperature: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class LlmCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID | None]
    api_key_id: Mapped[uuid.UUID | None]
    endpoint: Mapped[str]
    requested: Mapped[str]
    model_id: Mapped[uuid.UUID | None]
    model_name: Mapped[str | None]
    status: Mapped[str]
    http_status: Mapped[int]
    error_code: Mapped[str | None]
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    stream: Mapped[bool] = mapped_column(default=False)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    usage_source: Mapped[str] = mapped_column(default="provider")
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    ttft_ms: Mapped[int | None]
    latency_ms: Mapped[int]
    trace_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
