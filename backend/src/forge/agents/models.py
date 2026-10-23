from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class Agent(Base):
    __tablename__ = "agent"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    latest_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class AgentVersion(Base):
    __tablename__ = "agent_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    version: Mapped[int]
    config: Mapped[dict[str, Any]]
    prompt_id: Mapped[uuid.UUID | None]
    prompt_version: Mapped[int | None]
    note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class AgentTag(Base):
    __tablename__ = "agent_tag"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"), primary_key=True)
    project_id: Mapped[uuid.UUID]
    tag: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class Conversation(Base):
    __tablename__ = "conversation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    agent_version: Mapped[int]
    title: Mapped[str] = mapped_column(default="")
    source: Mapped[str] = mapped_column(default="app")  # app | mcp | widget
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    agent_version: Mapped[int]
    input: Mapped[str]
    output: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="queued")
    error_code: Mapped[str | None]
    error: Mapped[str | None]
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    limits: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    steps: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int | None]
    trace_id: Mapped[str | None]
    workflow_id: Mapped[str | None]
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class RunStep(Base):
    __tablename__ = "run_step"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    kind: Mapped[str]
    name: Mapped[str]
    status: Mapped[str]
    error_code: Mapped[str | None]
    input: Mapped[Any | None] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    model_name: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(default=0)
    ttft_ms: Mapped[int | None]
    llm_call_id: Mapped[uuid.UUID | None]
    tool_call_id: Mapped[str | None]
    span_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ToolCallLog(Base):
    __tablename__ = "tool_call_log"
    idempotency_key: Mapped[str] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id"))
    tool: Mapped[str]
    result: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RunFeedback(Base):
    __tablename__ = "run_feedback"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    rating: Mapped[str]
    comment: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AgentMemory(Base):
    """A fact an agent remembers about one user across conversations (E3.6)."""

    __tablename__ = "agent_memory"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    user_id: Mapped[uuid.UUID]
    content: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector())
    dims: Mapped[int]
    source_run_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(default=utcnow)
    use_count: Mapped[int] = mapped_column(default=0)
