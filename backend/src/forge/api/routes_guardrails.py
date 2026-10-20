"""Guardrail policy, a dry-run endpoint and the intervention log (M2.5)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from forge.api.deps import ProjectContext, project_permission, services
from forge.guardrails.service import (
    GuardrailEventOut,
    GuardrailPolicyIn,
    GuardrailPolicyOut,
    GuardrailTestIn,
    GuardrailTestOut,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/guardrails", tags=["guardrails"])

Reader = Annotated[ProjectContext, Depends(project_permission("project.manage"))]
Manager = Annotated[ProjectContext, Depends(project_permission("project.manage", writable=True))]


@router.get("/policy", response_model=GuardrailPolicyOut)
async def get_guardrail_policy(ctx: Reader, request: Request) -> GuardrailPolicyOut:
    return await services(request).guardrails.get_policy(ctx.project_id)


@router.put("/policy", response_model=GuardrailPolicyOut)
async def put_guardrail_policy(
    body: GuardrailPolicyIn, ctx: Manager, request: Request
) -> GuardrailPolicyOut:
    return await services(request).guardrails.put_policy(ctx.project_id, ctx.actor.id, body)


@router.post("/test", response_model=GuardrailTestOut)
async def test_guardrail_policy(
    body: GuardrailTestIn, ctx: Reader, request: Request
) -> GuardrailTestOut:
    """Dry run: nothing is logged and no model is called."""
    return await services(request).guardrails.test(ctx.project_id, body)


@router.get("/events", response_model=list[GuardrailEventOut])
async def events(ctx: Reader, request: Request, limit: int = 100) -> list[GuardrailEventOut]:
    return await services(request).guardrails.events(ctx.project_id, min(limit, 500))
