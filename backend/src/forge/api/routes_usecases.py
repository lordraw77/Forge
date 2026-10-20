"""Use cases API (M2.3a): catalogs and categories, access control by Role Name, use case
library with versions and lifecycle, end-user catalog view."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.usecases.schemas import (
    CatalogAccessIn,
    CatalogAccessOut,
    CatalogIn,
    CatalogOut,
    CatalogUseCase,
    CategoryIn,
    RunStartIn,
    UseCaseCreateIn,
    UseCaseDetail,
    UseCaseDuplicateIn,
    UseCasePage,
    UseCasePublishIn,
    UseCaseRunDetail,
    UseCaseRunOut,
    UseCaseStatusIn,
    UseCaseUpdateIn,
    UseCaseVersionDetail,
    UseCaseVersionDiff,
    UseCaseVersionSummary,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["use-cases"])

Builder = Annotated[ProjectContext, Depends(project_permission("usecase.build"))]
BuilderW = Annotated[ProjectContext, Depends(project_permission("usecase.build", writable=True))]
Publisher = Annotated[ProjectContext, Depends(project_permission("usecase.publish", writable=True))]
Runner = Annotated[ProjectContext, Depends(project_permission("usecase.run"))]
RunnerW = Annotated[ProjectContext, Depends(project_permission("usecase.run", writable=True))]
Admin = Annotated[ProjectContext, Depends(project_permission("project.manage", writable=True))]
AdminReader = Annotated[ProjectContext, Depends(project_permission("project.manage"))]


def _builds(ctx: ProjectContext) -> bool:
    return "usecase.build" in ctx.access.permissions


# ---- catalogs and categories
@router.get("/catalogs", response_model=list[CatalogOut])
async def list_catalogs(ctx: Runner, request: Request) -> list[CatalogOut]:
    return await services(request).usecases.list_catalogs(
        ctx.actor, ctx.project_id, see_all=_builds(ctx)
    )


@router.post("/catalogs", response_model=CatalogOut, status_code=status.HTTP_201_CREATED)
async def create_catalog(body: CatalogIn, ctx: Admin, request: Request) -> CatalogOut:
    return await services(request).usecases.create_catalog(ctx.actor, ctx.project_id, body)


@router.put("/catalogs/{catalog_id}", response_model=CatalogOut)
async def update_catalog(
    catalog_id: uuid.UUID, body: CatalogIn, ctx: Admin, request: Request
) -> CatalogOut:
    return await services(request).usecases.update_catalog(
        ctx.actor, ctx.project_id, catalog_id, body
    )


@router.delete("/catalogs/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog(catalog_id: uuid.UUID, ctx: Admin, request: Request) -> None:
    await services(request).usecases.delete_catalog(ctx.actor, ctx.project_id, catalog_id)


@router.post("/catalogs/{catalog_id}/categories", response_model=CatalogOut, status_code=201)
async def create_category(
    catalog_id: uuid.UUID, body: CategoryIn, ctx: Admin, request: Request
) -> CatalogOut:
    return await services(request).usecases.create_category(
        ctx.actor, ctx.project_id, catalog_id, body
    )


@router.put("/categories/{category_id}", response_model=CatalogOut)
async def update_category(
    category_id: uuid.UUID, body: CategoryIn, ctx: Admin, request: Request
) -> CatalogOut:
    return await services(request).usecases.update_category(
        ctx.actor, ctx.project_id, category_id, body
    )


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: uuid.UUID, ctx: Admin, request: Request) -> None:
    await services(request).usecases.delete_category(ctx.actor, ctx.project_id, category_id)


@router.get("/roles/{role_name_id}/catalog-access", response_model=CatalogAccessOut)
async def get_catalog_access(
    role_name_id: uuid.UUID, ctx: AdminReader, request: Request
) -> CatalogAccessOut:
    return await services(request).usecases.get_access(ctx.actor, ctx.project_id, role_name_id)


@router.put("/roles/{role_name_id}/catalog-access", response_model=CatalogAccessOut)
async def set_catalog_access(
    role_name_id: uuid.UUID, body: CatalogAccessIn, ctx: Admin, request: Request
) -> CatalogAccessOut:
    return await services(request).usecases.set_access(
        ctx.actor, ctx.project_id, role_name_id, body
    )


# ---- end-user catalog view
@router.get("/catalog/use-cases", response_model=list[CatalogUseCase])
async def catalog_use_cases(
    ctx: Runner,
    request: Request,
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    tag: str | None = None,
) -> list[CatalogUseCase]:
    return await services(request).usecases.catalog_view(
        ctx.actor, ctx.project_id, see_all=_builds(ctx), q=q, category_id=category_id, tag=tag
    )


@router.get("/catalog/use-cases/{use_case_id}", response_model=CatalogUseCase)
async def catalog_use_case(use_case_id: uuid.UUID, ctx: Runner, request: Request) -> CatalogUseCase:
    return await services(request).usecases.catalog_item(
        ctx.actor, ctx.project_id, use_case_id, see_all=_builds(ctx)
    )


# ---- library
@router.get("/use-cases", response_model=UseCasePage)
async def list_use_cases(
    ctx: Builder,
    request: Request,
    q: str | None = None,
    status_filter: Annotated[
        Literal["draft", "testing", "published", "deprecated"] | None, Query(alias="status")
    ] = None,
    catalog_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> UseCasePage:
    return await services(request).usecases.list_use_cases(
        ctx.actor,
        ctx.project_id,
        q=q,
        status=status_filter,
        catalog_id=catalog_id,
        category_id=category_id,
        since=since,
        until=until,
    )


@router.post("/use-cases", response_model=UseCaseDetail, status_code=status.HTTP_201_CREATED)
async def create_use_case(body: UseCaseCreateIn, ctx: BuilderW, request: Request) -> UseCaseDetail:
    return await services(request).usecases.create(ctx.actor, ctx.project_id, body)


@router.get("/use-cases/{use_case_id}", response_model=UseCaseDetail)
async def get_use_case(use_case_id: uuid.UUID, ctx: Builder, request: Request) -> UseCaseDetail:
    return await services(request).usecases.get(ctx.actor, ctx.project_id, use_case_id)


@router.put("/use-cases/{use_case_id}", response_model=UseCaseDetail)
async def update_use_case(
    use_case_id: uuid.UUID, body: UseCaseUpdateIn, ctx: BuilderW, request: Request
) -> UseCaseDetail:
    return await services(request).usecases.update(ctx.actor, ctx.project_id, use_case_id, body)


@router.delete("/use-cases/{use_case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_use_case(use_case_id: uuid.UUID, ctx: BuilderW, request: Request) -> None:
    await services(request).usecases.delete(ctx.actor, ctx.project_id, use_case_id)


@router.post("/use-cases/{use_case_id}/duplicate", response_model=UseCaseDetail, status_code=201)
async def duplicate_use_case(
    use_case_id: uuid.UUID, body: UseCaseDuplicateIn, ctx: BuilderW, request: Request
) -> UseCaseDetail:
    return await services(request).usecases.duplicate(
        ctx.actor, ctx.project_id, use_case_id, body.name
    )


@router.post("/use-cases/{use_case_id}/status", response_model=UseCaseDetail)
async def set_use_case_status(
    use_case_id: uuid.UUID, body: UseCaseStatusIn, ctx: Publisher, request: Request
) -> UseCaseDetail:
    return await services(request).usecases.set_status(
        ctx.actor, ctx.project_id, use_case_id, body.status
    )


@router.post("/use-cases/{use_case_id}/publish", response_model=UseCaseDetail)
async def publish_use_case(
    use_case_id: uuid.UUID, body: UseCasePublishIn, ctx: Publisher, request: Request
) -> UseCaseDetail:
    return await services(request).usecases.publish(
        ctx.actor, ctx.project_id, use_case_id, version=body.version, note=body.note
    )


@router.get("/use-cases/{use_case_id}/versions", response_model=list[UseCaseVersionSummary])
async def list_versions(
    use_case_id: uuid.UUID, ctx: Builder, request: Request
) -> list[UseCaseVersionSummary]:
    return await services(request).usecases.versions(ctx.actor, ctx.project_id, use_case_id)


@router.get("/use-cases/{use_case_id}/diff", response_model=UseCaseVersionDiff)
async def diff_versions(
    use_case_id: uuid.UUID, left: int, right: int, ctx: Builder, request: Request
) -> UseCaseVersionDiff:
    return await services(request).usecases.diff(
        ctx.actor, ctx.project_id, use_case_id, left, right
    )


@router.get("/use-cases/{use_case_id}/versions/{number}", response_model=UseCaseVersionDetail)
async def get_version(
    use_case_id: uuid.UUID, number: int, ctx: Builder, request: Request
) -> UseCaseVersionDetail:
    return await services(request).usecases.version(ctx.actor, ctx.project_id, use_case_id, number)


# ---- runs
def _sees_runs(ctx: ProjectContext) -> bool:
    return "run.read" in ctx.access.permissions


@router.post("/use-cases/{use_case_id}/runs", response_model=UseCaseRunDetail, status_code=202)
async def start_use_case_run(
    use_case_id: uuid.UUID, body: RunStartIn, ctx: RunnerW, request: Request
) -> UseCaseRunDetail:
    """Start a run. `published` (default) uses the live version with the catalog's visibility
    rules; `test` runs the draft and needs `usecase.build`."""
    return await services(request).usecase_runs.start(
        ctx.actor, ctx.project_id, use_case_id, body, can_build=_builds(ctx)
    )


@router.get("/use-case-runs", response_model=list[UseCaseRunOut])
async def list_use_case_runs(
    ctx: Runner,
    request: Request,
    use_case_id: uuid.UUID | None = None,
    status_filter: Annotated[
        Literal["queued", "running", "waiting_approval", "succeeded", "failed", "cancelled"] | None,
        Query(alias="status"),
    ] = None,
    limit: int = 50,
) -> list[UseCaseRunOut]:
    return await services(request).usecase_runs.list_runs(
        ctx.actor,
        ctx.project_id,
        see_all=_sees_runs(ctx),
        use_case_id=use_case_id,
        status=status_filter,
        limit=limit,
    )


@router.get("/use-case-runs/{run_id}", response_model=UseCaseRunDetail)
async def get_use_case_run(run_id: uuid.UUID, ctx: Runner, request: Request) -> UseCaseRunDetail:
    return await services(request).usecase_runs.get(
        ctx.actor, ctx.project_id, run_id, see_all=_sees_runs(ctx)
    )


@router.post("/use-case-runs/{run_id}/cancel", response_model=UseCaseRunDetail)
async def cancel_use_case_run(
    run_id: uuid.UUID, ctx: RunnerW, request: Request
) -> UseCaseRunDetail:
    return await services(request).usecase_runs.cancel(
        ctx.actor, ctx.project_id, run_id, see_all=_sees_runs(ctx)
    )
