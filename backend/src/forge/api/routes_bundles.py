"""Export and import of project configuration and assets (bundles).

Builders (`asset.write`) export and import prompts, RAG pipelines and collection definitions;
configuration (model aliases, gateway limits, user groups) also needs `project.manage`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from forge.api.deps import ProjectContext, project_permission, services
from forge.bundles import files as bundle_files
from forge.bundles.service import (
    BundlePlanIn,
    BundlePlanOut,
    BundleSyncIn,
    BundleSyncOut,
    ExportFilesIn,
    ExportIn,
    ImportIn,
    ImportReport,
)
from forge.core.errors import ProblemError

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["bundles"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
MAX_BUNDLE_BYTES = 20 * 1024 * 1024


@router.post("/export", response_model=dict[str, Any])
async def export_bundle(body: ExportIn, ctx: Reader, request: Request) -> JSONResponse:
    """A bundle (JSON) with the chosen items, downloaded as a file."""
    bundle = await services(request).bundles.export(
        ctx.actor, ctx.project_id, body, can_manage="project.manage" in ctx.access.permissions
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ctx.access.project.name.lower()).strip("-") or "project"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return JSONResponse(
        bundle,
        headers={"Content-Disposition": f'attachment; filename="forge-{slug}-{stamp}.json"'},
    )


@router.post("/import", response_model=ImportReport)
async def import_bundle(body: ImportIn, ctx: Writer, request: Request) -> ImportReport:
    """Dry run by default: the report says what would be created, extended, renamed or skipped
    and why an item cannot be imported. Send again with `dry_run: false` to apply."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BUNDLE_BYTES:
        raise ProblemError(413, "bundle_too_large", "Bundles are limited to 20 MB")
    return await services(request).bundles.import_(
        ctx.actor, ctx.project_id, body, can_manage="project.manage" in ctx.access.permissions
    )


# ---- assets as files and synchronisation (what `forgectl` does; E3.3)
@router.post("/export/files")
async def export_files(body: ExportFilesIn, ctx: Reader, request: Request) -> Response:
    """Every asset of the project as a zip of files in the Git layout (one JSON per item)."""
    data = await services(request).bundles.export_files(
        ctx.actor, ctx.project_id, body, can_manage="project.manage" in ctx.access.permissions
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ctx.access.project.name.lower()).strip("-") or "project"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="forge-{slug}-{stamp}.zip"'},
    )


@router.post("/sync/read", response_model=dict[str, Any])
async def read_files(files: list[UploadFile], ctx: Reader, request: Request) -> dict[str, Any]:
    """The files of a picked folder (path in each file name), or one zip, as a bundle."""
    uploaded: dict[str, bytes] = {}
    for f in files:
        data = await f.read(MAX_BUNDLE_BYTES + 1)
        if len(data) > MAX_BUNDLE_BYTES:
            raise ProblemError(413, "bundle_too_large", "Files are limited to 20 MB each")
        name = f.filename or ""
        if name.lower().endswith(".zip"):
            uploaded.update(bundle_files.unzip_files(data))
        elif path := bundle_files.normalise_path(name):
            uploaded[path] = data
        if len(uploaded) > bundle_files.MAX_FILES:
            raise ProblemError(413, "too_many_files", "Too many files")
    return services(request).bundles.read_files(uploaded)


@router.post("/sync/plan", response_model=BundlePlanOut)
async def sync_plan(body: BundlePlanIn, ctx: Reader, request: Request) -> BundlePlanOut:
    return await services(request).bundles.plan(
        ctx.actor, ctx.project_id, body, can_manage="project.manage" in ctx.access.permissions
    )


@router.post("/sync/apply", response_model=BundleSyncOut)
async def sync_apply(body: BundleSyncIn, ctx: Writer, request: Request) -> BundleSyncOut:
    """Dry run by default. Imports only new items and the versions the project lacks."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BUNDLE_BYTES:
        raise ProblemError(413, "bundle_too_large", "Bundles are limited to 20 MB")
    return await services(request).bundles.sync(
        ctx.actor, ctx.project_id, body, can_manage="project.manage" in ctx.access.permissions
    )
