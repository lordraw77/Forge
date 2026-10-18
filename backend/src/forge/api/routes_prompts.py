"""Prompt Studio (E1.10 to E1.13): library, versions, tags, test, playground, Help Me Write.

Versioned resource conventions from ADR-011. Everything needs `asset.write` (spec role
matrix: Project Admin and Builder); writes and runs also need a writable project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.prompts.service import (
    AssistIn,
    AssistOut,
    DuplicateIn,
    ParseIn,
    ParseOut,
    PlaygroundIn,
    PromptCreateIn,
    PromptDetail,
    PromptPage,
    PromptStudio,
    PromptUpdateIn,
    PublishIn,
    RunDetail,
    RunIn,
    RunOut,
    Status,
    TagIn,
    VersionIn,
    VersionOut,
    VersionSummary,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/prompts", tags=["prompts"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
MovableTag = Literal["staging", "prod"]


def _studio(request: Request) -> PromptStudio:
    return services(request).prompts


# ---- library
@router.get("", response_model=PromptPage)
async def list_prompts(
    ctx: Reader,
    request: Request,
    status_: Annotated[Status | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PromptPage:
    return await _studio(request).list_prompts(
        ctx.actor,
        ctx.project_id,
        status=status_,
        q=q,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )


@router.post("", response_model=PromptDetail, status_code=status.HTTP_201_CREATED)
async def create_prompt(body: PromptCreateIn, ctx: Writer, request: Request) -> PromptDetail:
    return await _studio(request).create(ctx.actor, ctx.project_id, body)


@router.post("/parse", response_model=ParseOut)
async def parse_prompt(body: ParseIn, _ctx: Reader) -> ParseOut:
    """Variables and template errors, for live feedback in the editor."""
    return PromptStudio.parse(body)


@router.post("/assist", response_model=AssistOut)
async def assist_prompt(body: AssistIn, ctx: Writer, request: Request) -> AssistOut:
    """Help Me Write on the project's `default-chat` alias (US-1.13.1)."""
    return await _studio(request).assist(ctx.actor, ctx.project_id, body)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_prompt_run(run_id: uuid.UUID, ctx: Reader, request: Request) -> RunDetail:
    """The call trace: latency, cost, usage, input messages, output, metadata."""
    return await _studio(request).run(ctx.actor, ctx.project_id, run_id)


@router.get("/{prompt_id}", response_model=PromptDetail)
async def get_prompt(prompt_id: uuid.UUID, ctx: Reader, request: Request) -> PromptDetail:
    return await _studio(request).get(ctx.actor, ctx.project_id, prompt_id)


@router.patch("/{prompt_id}", response_model=PromptDetail)
async def update_prompt(
    prompt_id: uuid.UUID, body: PromptUpdateIn, ctx: Writer, request: Request
) -> PromptDetail:
    return await _studio(request).update(ctx.actor, ctx.project_id, prompt_id, body)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(prompt_id: uuid.UUID, ctx: Writer, request: Request) -> None:
    await _studio(request).delete(ctx.actor, ctx.project_id, prompt_id)


@router.post("/{prompt_id}/duplicate", response_model=PromptDetail, status_code=201)
async def duplicate_prompt(
    prompt_id: uuid.UUID, body: DuplicateIn, ctx: Writer, request: Request
) -> PromptDetail:
    return await _studio(request).duplicate(ctx.actor, ctx.project_id, prompt_id, body.name)


# ---- versions and tags
@router.get("/{prompt_id}/versions", response_model=list[VersionSummary])
async def list_prompt_versions(
    prompt_id: uuid.UUID, ctx: Reader, request: Request
) -> list[VersionSummary]:
    return await _studio(request).versions(ctx.actor, ctx.project_id, prompt_id)


@router.post("/{prompt_id}/versions", response_model=PromptDetail, status_code=201)
async def save_prompt_version(
    prompt_id: uuid.UUID, body: VersionIn, ctx: Writer, request: Request
) -> PromptDetail:
    """Save = a new immutable version, tagged `draft` (US-1.10.2)."""
    return await _studio(request).save_version(ctx.actor, ctx.project_id, prompt_id, body)


@router.get("/{prompt_id}/versions/{version}", response_model=VersionOut)
async def get_prompt_version(
    prompt_id: uuid.UUID, version: int, ctx: Reader, request: Request
) -> VersionOut:
    return await _studio(request).version(ctx.actor, ctx.project_id, prompt_id, version)


@router.post("/{prompt_id}/publish", response_model=PromptDetail)
async def publish_prompt(
    prompt_id: uuid.UUID, body: PublishIn, ctx: Writer, request: Request
) -> PromptDetail:
    """Moves the `prod` tag: the prompt becomes Published in the library (US-1.11.2)."""
    return await _studio(request).set_tag(
        ctx.actor, ctx.project_id, prompt_id, "prod", body.version
    )


@router.put("/{prompt_id}/tags/{tag}", response_model=PromptDetail)
async def set_prompt_tag(
    prompt_id: uuid.UUID, tag: MovableTag, body: TagIn, ctx: Writer, request: Request
) -> PromptDetail:
    return await _studio(request).set_tag(ctx.actor, ctx.project_id, prompt_id, tag, body.version)


@router.delete("/{prompt_id}/tags/{tag}", response_model=PromptDetail)
async def remove_prompt_tag(
    prompt_id: uuid.UUID, tag: MovableTag, ctx: Writer, request: Request
) -> PromptDetail:
    return await _studio(request).remove_tag(ctx.actor, ctx.project_id, prompt_id, tag)


# ---- runs
@router.post("/{prompt_id}/test", response_model=RunDetail)
async def test_prompt(
    prompt_id: uuid.UUID, body: RunIn, ctx: Writer, request: Request
) -> RunDetail:
    """Render with the given variables and call the model; a model error is a failed run."""
    return await _studio(request).test(ctx.actor, ctx.project_id, prompt_id, body)


@router.post("/{prompt_id}/playground", response_model=list[RunDetail])
async def playground_prompt(
    prompt_id: uuid.UUID, body: PlaygroundIn, ctx: Writer, request: Request
) -> list[RunDetail]:
    """The same prompt on 2 to 4 models in parallel (US-1.12.1)."""
    return await _studio(request).playground(ctx.actor, ctx.project_id, prompt_id, body)


@router.get("/{prompt_id}/runs", response_model=list[RunOut])
async def list_prompt_runs(
    prompt_id: uuid.UUID,
    ctx: Reader,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[RunOut]:
    return await _studio(request).runs(ctx.actor, ctx.project_id, prompt_id, limit)
