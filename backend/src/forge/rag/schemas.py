"""RAG inputs and outputs (spec Part 2 "RAG Studio", "RAG design" defaults; US-1.14.x to 1.17.x).
Inputs forbid unknown fields (threat model TB1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SearchMethod = Literal["vector", "semantic", "hybrid"]
NodeParser = Literal["structure", "recursive", "fixed"]
ResponseMode = Literal["compact", "refine"]
DocStatus = Literal["pending", "processing", "ready", "failed"]
Tag = Literal["draft", "staging", "prod"]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ collections
class CollectionIn(In):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class CollectionUpdateIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class StatusCounts(Out):
    pending: int = 0
    processing: int = 0
    ready: int = 0
    failed: int = 0


class CollectionOut(Out):
    id: uuid.UUID
    name: str
    description: str
    status: Literal["empty", "in_progress", "success", "failed"]
    documents: StatusCounts
    size_bytes: int
    knowledge_bases: int
    created_at: datetime
    updated_at: datetime


class AclEntry(Out):
    kind: Literal["group", "role"]
    id: uuid.UUID
    name: str | None = Field(description="None when the group or role no longer exists")


class IngestionOut(Out):
    kb_id: uuid.UUID
    embedding_model: str
    status: DocStatus
    error: str | None
    chunks: int
    embedded: int
    reused: int
    finished_at: datetime | None


class DocumentOut(Out):
    id: uuid.UUID
    collection_id: uuid.UUID
    name: str
    path: str
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str
    acl: list[AclEntry]
    status: DocStatus
    error: str | None
    failed_at: datetime | None
    pages: int | None
    created_at: datetime
    updated_at: datetime
    ingestions: list[IngestionOut] = Field(default_factory=list)


class UploadResult(Out):
    filename: str
    status: Literal["accepted", "rejected", "unchanged"]
    document: DocumentOut | None = None
    error: str | None = None


class DocumentUpdateIn(In):
    """Who may read the document in RAG answers: members of these groups or with these roles.
    Both empty = the whole project (US-1.16.2)."""

    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    role_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class KnowledgeBaseOut(Out):
    id: uuid.UUID
    collection_id: uuid.UUID
    embedding_model: str
    dims: int
    chunk_size: int
    chunk_overlap: int
    node_parser: str
    status: Literal["building", "ready", "failed"]
    documents: StatusCounts
    chunks: int
    created_at: datetime
    ready_at: datetime | None


# ------------------------------------------------------------------ pipelines
class PipelineConfig(In):
    """Pipeline parameters (spec Part 2) with the "RAG design" defaults."""

    collection_id: uuid.UUID
    llm: str = Field(default="default-chat", min_length=1, max_length=100)
    embedding_model: str = Field(default="embedding", min_length=1, max_length=100)
    search_method: SearchMethod = "hybrid"
    reranker: str | None = Field(
        default="rerank", max_length=100, description="Model or alias; null disables reranking"
    )
    prompt_id: uuid.UUID | None = None
    chunk_size: int = Field(default=512, ge=100, le=2000)
    chunk_overlap: int = Field(default=64, ge=0, le=1000)
    node_parser: NodeParser = "structure"
    response_mode: ResponseMode = "compact"
    top_k: int = Field(default=6, ge=1, le=50)
    candidates: int = Field(default=40, ge=5, le=200)
    similarity_threshold: float = Field(default=0.30, ge=0, le=1)
    context_budget: int = Field(default=6000, ge=500, le=100_000)
    temperature: float | None = Field(default=0.1, ge=0, le=2)

    @model_validator(mode="after")
    def _overlap(self) -> PipelineConfig:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk overlap must be smaller than chunk size")
        return self


class PipelineCreateIn(In):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    config: PipelineConfig


class PipelineVersionIn(In):
    config: PipelineConfig
    note: str = Field(default="", max_length=500)


class PipelineUpdateIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class PipelineDuplicateIn(In):
    name: str = Field(min_length=1, max_length=120)


class PipelineTagIn(In):
    version: int = Field(ge=1)


class PipelinePublishIn(In):
    version: int | None = Field(default=None, ge=1)


class PipelineVersionOut(Out):
    version: int
    config: PipelineConfig
    kb_id: uuid.UUID
    kb_status: str
    note: str
    tags: list[Tag]
    created_at: datetime
    created_by: uuid.UUID | None


class PipelineOut(Out):
    id: uuid.UUID
    name: str
    description: str
    status: Literal["draft", "published"]
    latest_version: int
    published_version: int | None
    tags: dict[str, int]
    collection_name: str | None
    search_method: str
    created_at: datetime
    updated_at: datetime


class PipelineDetail(PipelineOut):
    version: PipelineVersionOut


class PipelinePage(Out):
    items: list[PipelineOut]
    next_cursor: str | None


class QueryIn(In):
    query: str = Field(min_length=1, max_length=4000)
    version: int | None = Field(default=None, ge=1)


class Citation(Out):
    index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    page: int | None
    heading_path: str
    score: float
    excerpt: str
    url: str


class RagRunOut(Out):
    id: uuid.UUID
    pipeline_id: uuid.UUID | None
    version: int | None
    query: str
    status: Literal["ok", "no_context", "error"]
    error_code: str | None
    error: str | None
    answer: str | None
    citations: list[Citation]
    model_name: str | None
    tokens_in: int
    tokens_out: int
    total_tokens: int
    cost: Decimal
    latency_ms: int
    created_at: datetime


class RagRunDetail(RagRunOut):
    retrieval: dict[str, Any]
    messages: list[dict[str, Any]]
