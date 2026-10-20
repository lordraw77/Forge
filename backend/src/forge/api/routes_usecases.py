"""Use cases API (M2.3a): catalogs and categories, access control by Role Name, use case
library with versions and lifecycle, end-user catalog view."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from forge.api.deps import ProjectContext, project_permission, services
from forge.core.errors import ProblemError
from forge.usecases import render
from forge.usecases.planner import PlanIn, PlanOut
from forge.usecases.schemas import (
    CatalogAccessIn,
    CatalogAccessOut,
    CatalogIn,
    CatalogOut,
    CatalogUseCase,
    CategoryIn,
    RunFileOut,
    RunStartIn,
    SessionDetail,
    SessionOut,
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


@router.put("/catalog/use-cases/{use_case_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def add_favorite(use_case_id: uuid.UUID, ctx: RunnerW, request: Request) -> None:
    await services(request).usecases.set_favorite(
        ctx.actor, ctx.project_id, use_case_id, True, see_all=_builds(ctx)
    )


@router.delete("/catalog/use-cases/{use_case_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(use_case_id: uuid.UUID, ctx: RunnerW, request: Request) -> None:
    await services(request).usecases.set_favorite(
        ctx.actor, ctx.project_id, use_case_id, False, see_all=_builds(ctx)
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


_SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def _sse(event: dict[str, Any], event_id: int) -> bytes:
    data = json.dumps(event, default=str)
    return f"id: {event_id}\nevent: {event['type']}\ndata: {data}\n\n".encode()


async def _event_stream(
    request: Request, events: AsyncIterator[dict[str, Any]]
) -> AsyncIterator[bytes]:
    n = 0
    async for event in events:
        if await request.is_disconnected():
            return
        n += 1
        yield _sse(event, n)


@router.post("/use-cases/{use_case_id}/runs", response_model=UseCaseRunDetail, status_code=202)
async def start_use_case_run(
    use_case_id: uuid.UUID,
    body: RunStartIn,
    ctx: RunnerW,
    request: Request,
    response: Response,
) -> UseCaseRunDetail | StreamingResponse:
    """Start a run. `published` (default) uses the live version with the catalog's visibility
    rules; `test` runs the draft and needs `usecase.build`.

    `delivery`: `async` answers 202 with the run (poll it, or give a `webhook_url`); `sync`
    waits up to `timeout_s` and answers 200 with the finished run, or 202 if it is still going;
    `stream` answers with server-sent events (`run`, `step`, `done`) until the run ends.
    """
    runs = services(request).usecase_runs
    inspect = _sees_runs(ctx)
    started = await runs.start(
        ctx.actor, ctx.project_id, use_case_id, body, can_build=_builds(ctx), inspect=inspect
    )
    if body.delivery == "stream":
        return StreamingResponse(
            _event_stream(
                request,
                runs.watch(
                    ctx.actor,
                    ctx.project_id,
                    started.id,
                    see_all=True,
                    inspect=inspect,
                ),
            ),
            status_code=200,
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    if body.delivery == "sync":
        done = await runs.wait(
            ctx.actor,
            ctx.project_id,
            started.id,
            see_all=True,
            inspect=inspect,
            timeout_s=body.timeout_s,
        )
        if done.status in ("succeeded", "failed", "cancelled"):
            response.status_code = status.HTTP_200_OK
        return done
    return started


@router.get("/use-case-runs", response_model=list[UseCaseRunOut])
async def list_use_case_runs(
    ctx: Runner,
    request: Request,
    use_case_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
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
        session_id=session_id,
        status=status_filter,
        limit=limit,
    )


@router.get("/use-case-runs/{run_id}", response_model=UseCaseRunDetail)
async def get_use_case_run(run_id: uuid.UUID, ctx: Runner, request: Request) -> UseCaseRunDetail:
    return await services(request).usecase_runs.get(
        ctx.actor, ctx.project_id, run_id, see_all=_sees_runs(ctx), inspect=_sees_runs(ctx)
    )


@router.get("/use-case-runs/{run_id}/events")
async def use_case_run_events(
    run_id: uuid.UUID, ctx: Runner, request: Request
) -> StreamingResponse:
    """Progress of a run as server-sent events: `run`, `step` (one per step start/end) and
    `done`. A reconnecting client gets the whole state again, so a refreshed page picks a
    running run up. End users get step names and status only, never agents or prompts."""
    runs = services(request).usecase_runs
    see_all = _sees_runs(ctx)
    await runs.get(ctx.actor, ctx.project_id, run_id, see_all=see_all, inspect=see_all)
    return StreamingResponse(
        _event_stream(
            request,
            runs.watch(ctx.actor, ctx.project_id, run_id, see_all=see_all, inspect=see_all),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/use-case-runs/{run_id}/cancel", response_model=UseCaseRunDetail)
async def cancel_use_case_run(
    run_id: uuid.UUID, ctx: RunnerW, request: Request
) -> UseCaseRunDetail:
    return await services(request).usecase_runs.cancel(
        ctx.actor, ctx.project_id, run_id, see_all=_sees_runs(ctx), inspect=_sees_runs(ctx)
    )


@router.post("/use-case-runs/{run_id}/rerun", response_model=UseCaseRunDetail, status_code=202)
async def rerun_use_case_run(run_id: uuid.UUID, ctx: RunnerW, request: Request) -> UseCaseRunDetail:
    """Run again with the same inputs on the version live now, in the same session."""
    return await services(request).usecase_runs.rerun(
        ctx.actor,
        ctx.project_id,
        run_id,
        see_all=_sees_runs(ctx),
        can_build=_builds(ctx),
        inspect=_sees_runs(ctx),
    )


def _attachment(name: str, content_type: str, size: int | None, body: Any) -> StreamingResponse:
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "'")
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if size is not None:
        headers["Content-Length"] = str(size)
    return StreamingResponse(body, media_type=content_type, headers=headers)


@router.get("/use-case-runs/{run_id}/export")
async def export_use_case_run(
    run_id: uuid.UUID,
    ctx: Runner,
    request: Request,
    fmt: Annotated[Literal["markdown", "json"], Query(alias="format")] = "markdown",
) -> StreamingResponse:
    """The final output of a finished run as a Markdown or JSON file."""
    run = await services(request).usecase_runs.get(
        ctx.actor, ctx.project_id, run_id, see_all=_sees_runs(ctx), inspect=False
    )
    if run.status != "succeeded" or run.outputs is None:
        raise ProblemError(409, "run_not_finished", "The run has no output to download")
    value = run.outputs.get("result")
    data = render.render(fmt, run.use_case_name, value)
    stem = run.use_case_name.replace(" ", "_")[:80]
    return _attachment(
        f"{stem}.{render.EXTENSIONS[fmt]}", render.CONTENT_TYPES[fmt], len(data), iter([data])
    )


# ---- sessions
@router.get("/use-case-sessions", response_model=list[SessionOut])
async def list_use_case_sessions(
    ctx: Runner, request: Request, use_case_id: uuid.UUID | None = None, limit: int = 50
) -> list[SessionOut]:
    """The caller's own sessions, latest first."""
    return await services(request).usecase_runs.list_sessions(
        ctx.actor, ctx.project_id, use_case_id=use_case_id, limit=limit
    )


@router.get("/use-case-sessions/{session_id}", response_model=SessionDetail)
async def get_use_case_session(
    session_id: uuid.UUID, ctx: Runner, request: Request
) -> SessionDetail:
    return await services(request).usecase_runs.get_session(ctx.actor, ctx.project_id, session_id)


# ---- files
@router.post("/use-case-files", response_model=RunFileOut, status_code=201)
async def upload_input_file(
    ctx: RunnerW, request: Request, file: Annotated[UploadFile, File()]
) -> RunFileOut:
    """Upload a file for a form field of type `file`; pass the returned id as the field value.
    Text, Markdown, CSV, JSON, PDF, DOCX and XLSX up to 100 MB."""
    return await services(request).usecase_runs.files.upload(
        ctx.actor, ctx.project_id, "input", file.filename or "file", file.file, file.content_type
    )


@router.get("/use-case-files/{file_id}/content")
async def download_use_case_file(
    file_id: uuid.UUID, ctx: Runner, request: Request
) -> StreamingResponse:
    """An output file of a run (same visibility as the run) or a file the caller uploaded."""
    item = await services(request).usecase_runs.files.download(
        ctx.actor, ctx.project_id, file_id, see_all=_sees_runs(ctx)
    )
    return _attachment(item.name, item.content_type, item.size, item.body)


@router.get("/use-cases/{use_case_id}/requirements-file", response_model=RunFileOut | None)
async def get_requirements_file(
    use_case_id: uuid.UUID, ctx: Builder, request: Request
) -> RunFileOut | None:
    return await services(request).usecase_runs.files.requirements_file(
        ctx.actor, ctx.project_id, use_case_id
    )


@router.post(
    "/use-cases/{use_case_id}/requirements-file", response_model=RunFileOut, status_code=201
)
async def upload_requirements_file(
    use_case_id: uuid.UUID, ctx: BuilderW, request: Request, file: Annotated[UploadFile, File()]
) -> RunFileOut:
    """The optional document attached to the requirements (up to 100 MB); a new one replaces
    the old. The Planner reads an excerpt of its text."""
    return await services(request).usecase_runs.files.upload(
        ctx.actor,
        ctx.project_id,
        "requirements",
        file.filename or "file",
        file.file,
        file.content_type,
        use_case_id=use_case_id,
    )


@router.delete("/use-cases/{use_case_id}/requirements-file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_requirements_file(use_case_id: uuid.UUID, ctx: BuilderW, request: Request) -> None:
    await services(request).usecase_runs.files.delete_requirements(
        ctx.actor, ctx.project_id, use_case_id
    )


# ---- builder
@router.get("/use-case-assets", response_model=dict[str, list[dict[str, Any]]])
async def use_case_assets(ctx: Builder, request: Request) -> dict[str, list[dict[str, Any]]]:
    """The published agents, tools and prompts a workflow can pin (name, version, description)."""
    return await services(request).usecases.assets(ctx.actor, ctx.project_id)


@router.post("/use-cases/{use_case_id}/plan", response_model=PlanOut)
async def plan_use_case(
    use_case_id: uuid.UUID, body: PlanIn, ctx: Builder, request: Request
) -> PlanOut:
    """Ask the Planner for a workflow (or a change); nothing is saved."""
    return await services(request).planner.plan(ctx.actor, ctx.project_id, use_case_id, body)
