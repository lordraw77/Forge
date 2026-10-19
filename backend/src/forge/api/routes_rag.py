"""RAG Studio (E1.17): pipelines with versions and tags, test queries with citations, traces.

Everything needs `asset.write`; writes and queries also need a writable project. Versioned
resource conventions from ADR-011.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.rag.service import (
    PipelineCreateIn,
    PipelineDetail,
    PipelineDuplicateIn,
    PipelinePage,
    PipelinePublishIn,
    PipelineTagIn,
    PipelineUpdateIn,
    PipelineVersionIn,
    PipelineVersionOut,
    QueryIn,
    RagRunDetail,
    RagRunOut,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/rag", tags=["rag"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
MovableTag = Literal["staging", "prod"]


@router.get("", response_model=PipelinePage)
async def list_rag_pipelines(
    ctx: Reader,
    request: Request,
    status_: Annotated[Literal["draft", "published"] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PipelinePage:
    return await services(request).rag.studio.list_pipelines(
        ctx.actor,
        ctx.project_id,
        status=status_,
        q=q,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )


@router.post("", response_model=PipelineDetail, status_code=status.HTTP_201_CREATED)
async def create_rag_pipeline(
    body: PipelineCreateIn, ctx: Writer, request: Request
) -> PipelineDetail:
    return await services(request).rag.studio.create(ctx.actor, ctx.project_id, body)


@router.get("/runs/{run_id}", response_model=RagRunDetail)
async def get_rag_run(run_id: uuid.UUID, ctx: Reader, request: Request) -> RagRunDetail:
    """The trace: retrieval ranking and timings, prompt messages, answer, cost and usage."""
    return await services(request).rag.studio.run(ctx.actor, ctx.project_id, run_id)


@router.get("/{pipeline_id}", response_model=PipelineDetail)
async def get_rag_pipeline(pipeline_id: uuid.UUID, ctx: Reader, request: Request) -> PipelineDetail:
    return await services(request).rag.studio.get(ctx.actor, ctx.project_id, pipeline_id)


@router.patch("/{pipeline_id}", response_model=PipelineDetail)
async def update_rag_pipeline(
    pipeline_id: uuid.UUID, body: PipelineUpdateIn, ctx: Writer, request: Request
) -> PipelineDetail:
    return await services(request).rag.studio.update(ctx.actor, ctx.project_id, pipeline_id, body)


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rag_pipeline(pipeline_id: uuid.UUID, ctx: Writer, request: Request) -> None:
    await services(request).rag.studio.delete(ctx.actor, ctx.project_id, pipeline_id)


@router.post("/{pipeline_id}/duplicate", response_model=PipelineDetail, status_code=201)
async def duplicate_rag_pipeline(
    pipeline_id: uuid.UUID, body: PipelineDuplicateIn, ctx: Writer, request: Request
) -> PipelineDetail:
    return await services(request).rag.studio.duplicate(
        ctx.actor, ctx.project_id, pipeline_id, body.name
    )


@router.get("/{pipeline_id}/versions", response_model=list[PipelineVersionOut])
async def list_rag_pipeline_versions(
    pipeline_id: uuid.UUID, ctx: Reader, request: Request
) -> list[PipelineVersionOut]:
    return await services(request).rag.studio.versions(ctx.actor, ctx.project_id, pipeline_id)


@router.post("/{pipeline_id}/versions", response_model=PipelineDetail, status_code=201)
async def save_rag_pipeline_version(
    pipeline_id: uuid.UUID, body: PipelineVersionIn, ctx: Writer, request: Request
) -> PipelineDetail:
    """Save Configuration = a new version; new index parameters start a new knowledge base."""
    return await services(request).rag.studio.save_version(
        ctx.actor, ctx.project_id, pipeline_id, body
    )


@router.post("/{pipeline_id}/publish", response_model=PipelineDetail)
async def publish_rag_pipeline(
    pipeline_id: uuid.UUID, body: PipelinePublishIn, ctx: Writer, request: Request
) -> PipelineDetail:
    return await services(request).rag.studio.set_tag(
        ctx.actor, ctx.project_id, pipeline_id, "prod", body.version
    )


@router.put("/{pipeline_id}/tags/{tag}", response_model=PipelineDetail)
async def set_rag_pipeline_tag(
    pipeline_id: uuid.UUID, tag: MovableTag, body: PipelineTagIn, ctx: Writer, request: Request
) -> PipelineDetail:
    return await services(request).rag.studio.set_tag(
        ctx.actor, ctx.project_id, pipeline_id, tag, body.version
    )


@router.delete("/{pipeline_id}/tags/{tag}", response_model=PipelineDetail)
async def remove_rag_pipeline_tag(
    pipeline_id: uuid.UUID, tag: MovableTag, ctx: Writer, request: Request
) -> PipelineDetail:
    return await services(request).rag.studio.remove_tag(
        ctx.actor, ctx.project_id, pipeline_id, tag
    )


@router.post("/{pipeline_id}/query", response_model=RagRunDetail)
async def query_rag_pipeline(
    pipeline_id: uuid.UUID, body: QueryIn, ctx: Writer, request: Request
) -> RagRunDetail:
    """Retrieve, answer with citations, record the run. Model errors are failed runs."""
    return await services(request).rag.studio.query(ctx.actor, ctx.project_id, pipeline_id, body)


@router.get("/{pipeline_id}/runs", response_model=list[RagRunOut])
async def list_rag_runs(
    pipeline_id: uuid.UUID,
    ctx: Reader,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[RagRunOut]:
    return await services(request).rag.studio.runs(ctx.actor, ctx.project_id, pipeline_id, limit)
