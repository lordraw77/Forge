"""Triggers of use cases: schedules and inbound tokens (E3.9)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.usecases.triggers import (
    FireIn,
    FireOut,
    TriggerIn,
    TriggerOut,
    TriggerUpdateIn,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["triggers"])
public_router = APIRouter(prefix="/api/v1/triggers", tags=["triggers"])

Builder = Annotated[ProjectContext, Depends(project_permission("usecase.build"))]
BuilderW = Annotated[ProjectContext, Depends(project_permission("usecase.build", writable=True))]


@router.get("/triggers", response_model=list[TriggerOut])
async def list_triggers(
    ctx: Builder, request: Request, use_case_id: uuid.UUID | None = None
) -> list[TriggerOut]:
    return await services(request).triggers.list(ctx.actor, ctx.project_id, use_case_id)


@router.post(
    "/use-cases/{use_case_id}/triggers",
    response_model=TriggerOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_trigger(
    use_case_id: uuid.UUID, body: TriggerIn, ctx: BuilderW, request: Request
) -> TriggerOut:
    return await services(request).triggers.create(ctx.actor, ctx.project_id, use_case_id, body)


@router.patch("/triggers/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: uuid.UUID, body: TriggerUpdateIn, ctx: BuilderW, request: Request
) -> TriggerOut:
    return await services(request).triggers.update(ctx.actor, ctx.project_id, trigger_id, body)


@router.delete("/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(trigger_id: uuid.UUID, ctx: BuilderW, request: Request) -> Response:
    await services(request).triggers.delete(ctx.actor, ctx.project_id, trigger_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/triggers/{trigger_id}/rotate-token", response_model=TriggerOut)
async def rotate_trigger_token(
    trigger_id: uuid.UUID, ctx: BuilderW, request: Request
) -> TriggerOut:
    return await services(request).triggers.rotate(ctx.actor, ctx.project_id, trigger_id)


@router.post(
    "/triggers/{trigger_id}/fire", response_model=FireOut, status_code=status.HTTP_202_ACCEPTED
)
async def fire_trigger(
    trigger_id: uuid.UUID, ctx: BuilderW, request: Request, body: FireIn | None = None
) -> FireOut:
    return await services(request).triggers.fire_now(
        ctx.actor, ctx.project_id, trigger_id, body or FireIn()
    )


@public_router.post("/{token}", response_model=FireOut, status_code=status.HTTP_202_ACCEPTED)
async def fire_by_token(token: str, request: Request, body: FireIn | None = None) -> FireOut:
    """Inbound trigger: the token in the URL is the credential (no session, no CSRF)."""
    return await services(request).triggers.fire_token(token, body or FireIn())
