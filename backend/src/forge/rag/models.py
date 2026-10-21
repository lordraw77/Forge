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


class DataCollection(Base):
    __tablename__ = "data_collection"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class Document(Base):
    __tablename__ = "rag_document"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_collection.id"))
    name: Mapped[str]
    path: Mapped[str] = mapped_column(default="")
    mime_type: Mapped[str]
    kind: Mapped[str]
    size_bytes: Mapped[int]
    sha256: Mapped[bytes]
    object_key: Mapped[str]
    acl_groups: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(default="pending")
    error: Mapped[str | None]
    failed_at: Mapped[datetime | None]
    pages: Mapped[int | None]
    parsed_key: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_collection.id"))
    embedding_model: Mapped[str]
    dims: Mapped[int]
    chunk_size: Mapped[int]
    chunk_overlap: Mapped[int]
    node_parser: Mapped[str]
    tokenizer: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="building")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    ready_at: Mapped[datetime | None]


class Ingestion(Base):
    __tablename__ = "rag_ingestion"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_document.id"), primary_key=True)
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"), primary_key=True)
    project_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(default="pending")
    error: Mapped[str | None]
    chunks: Mapped[int] = mapped_column(default=0)
    embedded: Mapped[int] = mapped_column(default=0)
    reused: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class Chunk(Base):
    __tablename__ = "rag_chunk"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_document.id"))
    ordinal: Mapped[int]
    content: Mapped[str]
    heading_path: Mapped[str] = mapped_column(default="")
    page: Mapped[int | None]
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    acl_groups: Mapped[list[str]]
    content_hash: Mapped[bytes]
    token_count: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(Vector())


class Pipeline(Base):
    __tablename__ = "rag_pipeline"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    latest_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class PipelineVersion(Base):
    __tablename__ = "rag_pipeline_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_pipeline.id"))
    version: Mapped[int]
    config: Mapped[dict[str, Any]]
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_base.id"))
    note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class PipelineTag(Base):
    __tablename__ = "rag_pipeline_tag"
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_pipeline.id"), primary_key=True)
    project_id: Mapped[uuid.UUID]
    tag: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class RagRun(Base):
    __tablename__ = "rag_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    pipeline_id: Mapped[uuid.UUID | None]
    version: Mapped[int | None]
    kb_id: Mapped[uuid.UUID | None]
    query: Mapped[str]
    status: Mapped[str]
    error_code: Mapped[str | None]
    error: Mapped[str | None]
    answer: Mapped[str | None]
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    retrieval: Mapped[dict[str, Any]] = mapped_column(default=dict)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    model_name: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class Source(Base):
    """A scheduled external source feeding a collection (E2.13)."""

    __tablename__ = "rag_source"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_collection.id"))
    name: Mapped[str]
    type: Mapped[str]
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secret_id: Mapped[uuid.UUID | None]
    interval_minutes: Mapped[int | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    acl_groups: Mapped[list[str]] = mapped_column(default=list)
    delete_missing: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(default="idle")
    next_run_at: Mapped[datetime | None]
    last_run_at: Mapped[datetime | None]
    last_status: Mapped[str | None]
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SourceItem(Base):
    __tablename__ = "rag_source_item"
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_source.id"), primary_key=True)
    external_id: Mapped[str] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_document.id"))
    version: Mapped[str]
    seen_run: Mapped[uuid.UUID | None]


class SourceRun(Base):
    __tablename__ = "rag_source_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rag_source.id"))
    trigger: Mapped[str]
    status: Mapped[str] = mapped_column(default="running")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    added: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    unchanged: Mapped[int] = mapped_column(default=0)
    deleted: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None]
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
