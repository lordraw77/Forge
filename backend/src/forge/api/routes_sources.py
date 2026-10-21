"""Scheduled RAG sources (M2.6b, E2.13)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.rag.sources.service import (
    SourceIn,
    SourceOut,
    SourceRunOut,
    SourceService,
    SourceTypes,
    SourceUpdateIn,
    SyncIn,
    SyncOut,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/sources", tags=["sources"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]


def _svc(request: Request) -> SourceService:
    return services(request).rag.sources


@router.get("/types", response_model=SourceTypes)
async def source_types(ctx: Reader, request: Request) -> SourceTypes:
    return _svc(request).types(ctx.project_id)


@router.get("", response_model=list[SourceOut])
async def list_sources(ctx: Reader, request: Request) -> list[SourceOut]:
    return await _svc(request).list_sources(ctx.actor, ctx.project_id)


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceIn, ctx: Writer, request: Request) -> SourceOut:
    return await _svc(request).create(ctx.actor, ctx.project_id, body)


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: uuid.UUID, ctx: Reader, request: Request) -> SourceOut:
    return await _svc(request).get(ctx.actor, ctx.project_id, source_id)


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: uuid.UUID, body: SourceUpdateIn, ctx: Writer, request: Request
) -> SourceOut:
    return await _svc(request).update(ctx.actor, ctx.project_id, source_id, body)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID, ctx: Writer, request: Request, delete_documents: bool = False
) -> Response:
    await _svc(request).delete(ctx.actor, ctx.project_id, source_id, delete_documents)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{source_id}/sync", response_model=SyncOut, status_code=status.HTTP_202_ACCEPTED)
async def sync_source(
    source_id: uuid.UUID, ctx: Writer, request: Request, body: SyncIn | None = None
) -> SyncOut:
    return await _svc(request).sync(ctx.actor, ctx.project_id, source_id, body or SyncIn())


@router.get("/{source_id}/runs", response_model=list[SourceRunOut])
async def source_runs(
    source_id: uuid.UUID, ctx: Reader, request: Request, limit: int = 20
) -> list[SourceRunOut]:
    return await _svc(request).runs(ctx.actor, ctx.project_id, source_id, limit)
