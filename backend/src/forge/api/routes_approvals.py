"""Approvals API (M2.4): the inbox of people who may decide, the decision itself, and the public
review page behind the signed one-time links of approval emails."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from forge.api.deps import ProjectContext, project_permission, services
from forge.usecases.schemas import ApprovalDetail, ApprovalOut, DecisionIn

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["approvals"])
public_router = APIRouter(prefix="/api/v1/approval-links", tags=["approvals"])

Approver = Annotated[ProjectContext, Depends(project_permission("approval.decide"))]
ApproverW = Annotated[ProjectContext, Depends(project_permission("approval.decide", writable=True))]


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    ctx: Approver, request: Request, pending: bool = True, limit: int = 100
) -> list[ApprovalOut]:
    """Approvals the caller may decide: pending ones, or the latest of any state."""
    return await services(request).approvals.list(
        ctx.actor, ctx.project_id, ctx.access, pending_only=pending, limit=limit
    )


@router.get("/approvals/{approval_id}", response_model=ApprovalDetail)
async def get_approval(approval_id: uuid.UUID, ctx: Approver, request: Request) -> ApprovalDetail:
    return await services(request).approvals.get(ctx.actor, ctx.project_id, ctx.access, approval_id)


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalDetail)
async def decide(
    approval_id: uuid.UUID, body: DecisionIn, ctx: ApproverW, request: Request
) -> ApprovalDetail:
    """Approve (optionally with edits) or reject with feedback. 409 when already decided."""
    via = "api" if ctx.principal.kind == "api_key" else "ui"
    return await services(request).approvals.decide(
        ctx.actor, ctx.project_id, ctx.access, approval_id, body, via=via
    )


# ---- public: the token is the credential, opening a link never decides
@public_router.get("/{token}", response_model=ApprovalDetail)
async def view_link(token: str, request: Request) -> ApprovalDetail:
    return await services(request).approvals.link_view(token)


@public_router.post("/{token}/decision", response_model=ApprovalDetail)
async def decide_link(token: str, body: DecisionIn, request: Request) -> ApprovalDetail:
    return await services(request).approvals.link_decide(token, body)
