from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class ToolPolicy(Base):
    __tablename__ = "tool_policy"
    project_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    allowed_hosts: Mapped[list[str]] = mapped_column(default=list)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class Credential(Base):
    __tablename__ = "credential"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    kind: Mapped[str]
    param_name: Mapped[str | None]
    username: Mapped[str | None]
    secret_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class DataSource(Base):
    __tablename__ = "datasource"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    kind: Mapped[str] = mapped_column(default="postgres")
    host: Mapped[str]
    port: Mapped[int] = mapped_column(default=5432)
    database: Mapped[str]
    username: Mapped[str]
    ssl_mode: Mapped[str] = mapped_column(default="require")
    secret_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class McpServer(Base):
    __tablename__ = "mcp_server"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    transport: Mapped[str]
    url: Mapped[str | None]
    command: Mapped[str | None]
    args: Mapped[list[str]] = mapped_column(default=list)
    credential_id: Mapped[uuid.UUID | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class Tool(Base):
    __tablename__ = "tool"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    kind: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    latest_version: Mapped[int] = mapped_column(default=0)
    published_version: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class ToolVersion(Base):
    __tablename__ = "tool_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    tool_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool.id"))
    version: Mapped[int]
    description: Mapped[str]
    config: Mapped[dict[str, Any]]
    schema_in: Mapped[dict[str, Any]]
    timeout_s: Mapped[int] = mapped_column(default=15)
    requires_approval: Mapped[bool] = mapped_column(default=False)
    writes: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class ToolCall(Base):
    __tablename__ = "tool_call"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    tool_id: Mapped[uuid.UUID | None]
    tool_name: Mapped[str]
    version: Mapped[int | None]
    source: Mapped[str]
    run_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None]
    status: Mapped[str]
    error_code: Mapped[str | None]
    input: Mapped[dict[str, Any] | None]
    output: Mapped[dict[str, Any] | None]
    latency_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
