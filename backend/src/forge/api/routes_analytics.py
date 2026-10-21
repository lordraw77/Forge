"""Dashboards, Model Insights and activity (M2.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from forge.analytics.service import ActivityItem, ModelInsights, ProjectDashboard
from forge.api.deps import (
    Principal,
    ProjectContext,
    project_permission,
    ready_principal,
    services,
    super_admin,
)
from forge.core.errors import ProblemError

project_router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["analytics"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["analytics"])
me_router = APIRouter(prefix="/api/v1/me", tags=["analytics"])

Reader = Annotated[ProjectContext, Depends(project_permission("project.manage"))]
Admin = Annotated[Principal, Depends(super_admin)]
Ready = Annotated[Principal, Depends(ready_principal)]


@project_router.get("/analytics", response_model=ProjectDashboard)
async def project_dashboard(
    ctx: Reader, request: Request, since: datetime | None = None, until: datetime | None = None
) -> ProjectDashboard:
    return await services(request).analytics.project_dashboard(
        ctx.project_id, ctx.actor.id, since, until
    )


@project_router.get("/activity", response_model=list[ActivityItem])
async def project_activity(
    ctx: Reader,
    request: Request,
    since: datetime | None = None,
    q: str | None = None,
    limit: int = 100,
) -> list[ActivityItem]:
    return await services(request).analytics.project_activity(
        ctx.project_id, ctx.actor.id, since, q, limit
    )


@admin_router.get("/model-insights", response_model=ModelInsights)
async def model_insights(
    p: Admin, request: Request, since: datetime | None = None, until: datetime | None = None
) -> ModelInsights:
    assert p.actor.id is not None
    return await services(request).analytics.model_insights(p.actor.id, since, until)


@me_router.get("/activity", response_model=list[ActivityItem])
async def my_activity(
    p: Ready, request: Request, days: int = 15, limit: int = 50
) -> list[ActivityItem]:
    if p.actor.id is None:
        raise ProblemError(403, "forbidden", "Sign in to see your activity")
    return await services(request).analytics.my_activity(p.actor.id, days, limit)
