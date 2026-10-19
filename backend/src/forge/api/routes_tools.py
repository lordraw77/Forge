"""Tools Studio API (M2.2): library, credentials, egress policy, data sources, MCP servers,
OpenAPI import, test console and call log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.tools.schemas import (
    CredentialIn,
    CredentialOut,
    DataSourceIn,
    DataSourceOut,
    McpImportIn,
    McpServerIn,
    McpServerOut,
    McpToolOut,
    OpenApiImportIn,
    OpenApiImportOut,
    OpenApiPreviewIn,
    OpenApiPreviewOut,
    PolicyIn,
    PolicyOut,
    ToolCallOut,
    ToolCreateIn,
    ToolDetail,
    ToolDuplicateIn,
    ToolOut,
    ToolPage,
    ToolPublishIn,
    ToolTestIn,
    ToolTestOut,
    ToolVersionIn,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["tools"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
Admin = Annotated[ProjectContext, Depends(project_permission("project.manage", writable=True))]
AdminReader = Annotated[ProjectContext, Depends(project_permission("project.manage"))]


# ---- library
@router.get("/tools", response_model=ToolPage)
async def list_tools(
    ctx: Reader,
    request: Request,
    q: str | None = None,
    kind: Literal["http", "sql", "mcp"] | None = None,
    published: bool | None = None,
) -> ToolPage:
    return await services(request).tools.list_tools(
        ctx.actor, ctx.project_id, q=q, kind=kind, published=published
    )


@router.post("/tools", response_model=ToolDetail, status_code=status.HTTP_201_CREATED)
async def create_tool(body: ToolCreateIn, ctx: Writer, request: Request) -> ToolDetail:
    return await services(request).tools.create(ctx.actor, ctx.project_id, body)


@router.get("/tools/{tool_id}", response_model=ToolDetail)
async def get_tool(
    tool_id: uuid.UUID, ctx: Reader, request: Request, version: int | None = None
) -> ToolDetail:
    return await services(request).tools.get(ctx.actor, ctx.project_id, tool_id, version)


@router.post("/tools/{tool_id}/versions", response_model=ToolDetail, status_code=201)
async def save_tool_version(
    tool_id: uuid.UUID, body: ToolVersionIn, ctx: Writer, request: Request
) -> ToolDetail:
    return await services(request).tools.save_version(ctx.actor, ctx.project_id, tool_id, body)


@router.post("/tools/{tool_id}/publish", response_model=ToolDetail)
async def publish_tool(
    tool_id: uuid.UUID, body: ToolPublishIn, ctx: Writer, request: Request
) -> ToolDetail:
    return await services(request).tools.publish(ctx.actor, ctx.project_id, tool_id, body.version)


@router.delete("/tools/{tool_id}/publish", response_model=ToolDetail)
async def unpublish_tool(tool_id: uuid.UUID, ctx: Writer, request: Request) -> ToolDetail:
    return await services(request).tools.unpublish(ctx.actor, ctx.project_id, tool_id)


@router.post("/tools/{tool_id}/duplicate", response_model=ToolDetail, status_code=201)
async def duplicate_tool(
    tool_id: uuid.UUID, body: ToolDuplicateIn, ctx: Writer, request: Request
) -> ToolDetail:
    return await services(request).tools.duplicate(ctx.actor, ctx.project_id, tool_id, body.name)


@router.delete("/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: uuid.UUID, ctx: Writer, request: Request) -> None:
    await services(request).tools.delete(ctx.actor, ctx.project_id, tool_id)


@router.post("/tools/{tool_id}/test", response_model=ToolTestOut)
async def try_tool(
    tool_id: uuid.UUID, body: ToolTestIn, ctx: Writer, request: Request
) -> ToolTestOut:
    result, call_id = await services(request).tools.test(
        ctx.actor, ctx.project_id, tool_id, body.version, body.arguments
    )
    return ToolTestOut(
        status=result.status,
        error_code=result.error_code,
        content=result.content,
        output=result.output,
        request=result.request,
        latency_ms=result.latency_ms,
        call_id=call_id,
    )


@router.get("/tool-calls", response_model=list[ToolCallOut])
async def list_tool_calls(
    ctx: Reader,
    request: Request,
    tool_id: uuid.UUID | None = None,
    status_filter: Annotated[Literal["ok", "error"] | None, Query(alias="status")] = None,
    source: Literal["test", "agent"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[ToolCallOut]:
    rows = await services(request).tools.list_calls(
        ctx.actor,
        ctx.project_id,
        tool_id=tool_id,
        status=status_filter,
        source=source,
        since=since,
        until=until,
        limit=limit,
    )
    return [ToolCallOut.model_validate(r) for r in rows]


# ---- OpenAPI import
@router.post("/tool-import/openapi/preview", response_model=OpenApiPreviewOut)
async def preview_openapi(
    body: OpenApiPreviewIn, ctx: Writer, request: Request
) -> OpenApiPreviewOut:
    return services(request).tools.preview_openapi(body.document)


@router.post(
    "/tool-import/openapi", response_model=OpenApiImportOut, status_code=status.HTTP_201_CREATED
)
async def import_openapi(body: OpenApiImportIn, ctx: Writer, request: Request) -> OpenApiImportOut:
    created, skipped = await services(request).tools.import_openapi(ctx.actor, ctx.project_id, body)
    return OpenApiImportOut(created=created, skipped=skipped)


# ---- egress policy
@router.get("/tool-policy", response_model=PolicyOut)
async def get_policy(ctx: Reader, request: Request) -> PolicyOut:
    hosts, private = await services(request).tools.get_policy(ctx.actor, ctx.project_id)
    return PolicyOut(allowed_hosts=hosts, allow_private=private)


@router.put("/tool-policy", response_model=PolicyOut)
async def set_policy(body: PolicyIn, ctx: Admin, request: Request) -> PolicyOut:
    svc = services(request).tools
    hosts = await svc.set_policy(ctx.actor, ctx.project_id, body)
    return PolicyOut(allowed_hosts=hosts, allow_private=svc.allow_private)


# ---- credentials
@router.get("/tool-credentials", response_model=list[CredentialOut])
async def list_credentials(ctx: Reader, request: Request) -> list[CredentialOut]:
    rows = await services(request).tools.list_credentials(ctx.actor, ctx.project_id)
    return [
        CredentialOut(
            id=c.id,
            name=c.name,
            kind=c.kind,
            username=c.username,
            param_name=c.param_name,
            used_by=n,
            created_at=c.created_at,
        )
        for c, n in rows
    ]


@router.post("/tool-credentials", response_model=CredentialOut, status_code=201)
async def create_credential(body: CredentialIn, ctx: Admin, request: Request) -> CredentialOut:
    c = await services(request).tools.create_credential(ctx.actor, ctx.project_id, body)
    return CredentialOut(
        id=c.id,
        name=c.name,
        kind=c.kind,
        username=c.username,
        param_name=c.param_name,
        created_at=c.created_at,
    )


@router.delete("/tool-credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(credential_id: uuid.UUID, ctx: Admin, request: Request) -> None:
    await services(request).tools.delete_credential(ctx.actor, ctx.project_id, credential_id)


# ---- data sources
def _source_out(d: object, used: int = 0) -> DataSourceOut:
    out = DataSourceOut.model_validate(d)
    out.used_by = used
    return out


@router.get("/datasources", response_model=list[DataSourceOut])
async def list_datasources(ctx: Reader, request: Request) -> list[DataSourceOut]:
    rows = await services(request).tools.list_datasources(ctx.actor, ctx.project_id)
    return [_source_out(d, n) for d, n in rows]


@router.post("/datasources", response_model=DataSourceOut, status_code=201)
async def create_datasource(body: DataSourceIn, ctx: Admin, request: Request) -> DataSourceOut:
    return _source_out(
        await services(request).tools.save_datasource(ctx.actor, ctx.project_id, body)
    )


@router.put("/datasources/{source_id}", response_model=DataSourceOut)
async def update_datasource(
    source_id: uuid.UUID, body: DataSourceIn, ctx: Admin, request: Request
) -> DataSourceOut:
    return _source_out(
        await services(request).tools.save_datasource(ctx.actor, ctx.project_id, body, source_id)
    )


@router.delete("/datasources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datasource(source_id: uuid.UUID, ctx: Admin, request: Request) -> None:
    await services(request).tools.delete_datasource(ctx.actor, ctx.project_id, source_id)


# ---- MCP servers
def _server_out(m: object, credential: str | None, used: int = 0) -> McpServerOut:
    out = McpServerOut.model_validate({**vars(m), "credential": credential})
    out.used_by = used
    return out


@router.get("/mcp-servers", response_model=list[McpServerOut])
async def list_mcp_servers(ctx: Reader, request: Request) -> list[McpServerOut]:
    rows = await services(request).tools.list_servers(ctx.actor, ctx.project_id)
    return [_server_out(m, c, n) for m, c, n in rows]


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
async def create_mcp_server(body: McpServerIn, ctx: Admin, request: Request) -> McpServerOut:
    m = await services(request).tools.save_server(ctx.actor, ctx.project_id, body)
    return _server_out(m, body.credential)


@router.put("/mcp-servers/{server_id}", response_model=McpServerOut)
async def update_mcp_server(
    server_id: uuid.UUID, body: McpServerIn, ctx: Admin, request: Request
) -> McpServerOut:
    m = await services(request).tools.save_server(ctx.actor, ctx.project_id, body, server_id)
    return _server_out(m, body.credential)


@router.delete("/mcp-servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(server_id: uuid.UUID, ctx: Admin, request: Request) -> None:
    await services(request).tools.delete_server(ctx.actor, ctx.project_id, server_id)


@router.post("/mcp-servers/{server_id}/discover", response_model=list[McpToolOut])
async def discover_mcp_tools(
    server_id: uuid.UUID, ctx: Writer, request: Request
) -> list[McpToolOut]:
    return await services(request).tools.discover(ctx.actor, ctx.project_id, server_id)


@router.post("/mcp-servers/{server_id}/import", response_model=list[ToolOut], status_code=201)
async def import_mcp_tools(
    server_id: uuid.UUID, body: McpImportIn, ctx: Writer, request: Request
) -> list[ToolOut]:
    return await services(request).tools.import_mcp_tools(
        ctx.actor, ctx.project_id, server_id, body.tools
    )
