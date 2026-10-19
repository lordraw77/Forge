"""Export and import of project configuration and assets (bundles).

Builders (`asset.write`) export and import prompts, RAG pipelines and collection definitions;
configuration (model aliases, gateway limits, user groups) also needs `project.manage`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from forge.api.deps import ProjectContext, project_permission, services
from forge.bundles.service import ExportIn, ImportIn, ImportReport
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
