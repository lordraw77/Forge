"""The project's MCP server and the list of what it exposes (E3.5)."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from forge.api.deps import ProjectContext, project_permission, services
from forge.mcp_server.protocol import PARSE_ERROR, failure
from forge.mcp_server.service import ExposureIn, ExposureOut, ExposureUpdateIn

router = APIRouter(prefix="/api/v1/projects/{project_id}/mcp", tags=["mcp"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
Caller = Annotated[ProjectContext, Depends(project_permission("mcp.invoke", writable=True))]


@router.get("/exposures", response_model=list[ExposureOut])
async def list_exposures(ctx: Reader, request: Request) -> list[ExposureOut]:
    return await services(request).mcp_exposures.list(ctx.actor, ctx.project_id)


@router.post("/exposures", response_model=ExposureOut, status_code=status.HTTP_201_CREATED)
async def add_exposure(body: ExposureIn, ctx: Writer, request: Request) -> ExposureOut:
    return await services(request).mcp_exposures.add(ctx.actor, ctx.project_id, body)


@router.patch("/exposures/{exposure_id}", response_model=ExposureOut)
async def update_exposure(
    exposure_id: uuid.UUID, body: ExposureUpdateIn, ctx: Writer, request: Request
) -> ExposureOut:
    return await services(request).mcp_exposures.update(
        ctx.actor, ctx.project_id, exposure_id, body
    )


@router.delete("/exposures/{exposure_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_exposure(exposure_id: uuid.UUID, ctx: Writer, request: Request) -> Response:
    await services(request).mcp_exposures.remove(ctx.actor, ctx.project_id, exposure_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", include_in_schema=True, response_model=None)
async def mcp_endpoint(ctx: Caller, request: Request) -> Response:
    """MCP over Streamable HTTP (JSON-RPC, answered without a session). Authenticate with a
    project API key: `Authorization: Bearer fk_…`. The key needs the `mcp.invoke` scope."""
    try:
        payload = json.loads(await request.body())
    except ValueError:
        return JSONResponse(failure(None, PARSE_ERROR, "Parse error"), status_code=400)
    code, answer = await services(request).mcp.handle_http(
        ctx.actor, ctx.project_id, payload, request.headers
    )
    if answer is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(answer, status_code=code)


@router.api_route("", methods=["GET", "DELETE"], include_in_schema=False)
async def mcp_no_stream() -> Response:
    """No server-initiated stream and no session to end: say so, as the transport allows."""
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, headers={"Allow": "POST"})
