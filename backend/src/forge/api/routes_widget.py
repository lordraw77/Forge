"""The embeddable chat widget: its configuration, and the public calls the browser script
makes (E3.5). The public calls have no session or CSRF; the widget's key names what they may
reach, and the browser's `Origin` must be one of the widget's allowed origins."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from forge.api.deps import ProjectContext, project_permission, services
from forge.core.errors import ProblemError
from forge.widget.models import ChatWidget
from forge.widget.script import WIDGET_JS
from forge.widget.service import (
    WidgetAnswerOut,
    WidgetIn,
    WidgetMessageIn,
    WidgetMessageOut,
    WidgetOut,
    WidgetPublicConfig,
    WidgetSessionOut,
    WidgetUpdateIn,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/widgets", tags=["widget"])
public_router = APIRouter(prefix="/api/v1/widget", tags=["widget"])

Reader = Annotated[ProjectContext, Depends(project_permission("project.manage"))]
Writer = Annotated[ProjectContext, Depends(project_permission("project.manage", writable=True))]


@router.get("", response_model=list[WidgetOut])
async def list_widgets(ctx: Reader, request: Request) -> list[WidgetOut]:
    return await services(request).widgets.list(ctx.actor, ctx.project_id)


@router.post("", response_model=WidgetOut, status_code=status.HTTP_201_CREATED)
async def create_widget(body: WidgetIn, ctx: Writer, request: Request) -> WidgetOut:
    return await services(request).widgets.create(ctx.actor, ctx.project_id, body)


@router.patch("/{widget_id}", response_model=WidgetOut)
async def update_widget(
    widget_id: uuid.UUID, body: WidgetUpdateIn, ctx: Writer, request: Request
) -> WidgetOut:
    return await services(request).widgets.update(ctx.actor, ctx.project_id, widget_id, body)


@router.delete("/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(widget_id: uuid.UUID, ctx: Writer, request: Request) -> Response:
    await services(request).widgets.delete(ctx.actor, ctx.project_id, widget_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- public side
@public_router.get("/widget.js", include_in_schema=False)
async def widget_script() -> Response:
    return Response(
        WIDGET_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def _cors(request: Request, widget: ChatWidget) -> dict[str, str]:
    origin = request.headers.get("origin")
    svc = services(request).widgets
    if not svc.origin_allowed(widget, origin):
        raise ProblemError(403, "origin_not_allowed", "This site is not allowed to use the widget")
    if origin is None:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def _visitor(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@public_router.options("/{key}/{rest:path}", include_in_schema=False)
async def widget_preflight(key: str, rest: str, request: Request) -> Response:
    widget = await services(request).widgets.find(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_cors(request, widget))


@public_router.get("/{key}/config", response_model=WidgetPublicConfig)
async def widget_config(key: str, request: Request) -> JSONResponse:
    widget = await services(request).widgets.find(key)
    headers = _cors(request, widget)
    return JSONResponse(
        WidgetPublicConfig(title=widget.title or widget.name, welcome=widget.welcome).model_dump(),
        headers=headers,
    )


@public_router.post("/{key}/session", response_model=WidgetSessionOut)
async def widget_session(key: str, request: Request) -> JSONResponse:
    svc = services(request).widgets
    widget = await svc.find(key)
    headers = _cors(request, widget)
    out = await svc.open_session(widget)
    return JSONResponse(out.model_dump(), headers=headers)


@public_router.post("/{key}/messages", response_model=WidgetMessageOut)
async def widget_message(key: str, body: WidgetMessageIn, request: Request) -> JSONResponse:
    svc = services(request).widgets
    widget = await svc.find(key)
    headers = _cors(request, widget)
    out = await svc.send(widget, _visitor(request), body)
    return JSONResponse(out.model_dump(mode="json"), headers=headers)


@public_router.get("/{key}/runs/{run_id}", response_model=WidgetAnswerOut)
async def widget_answer(
    key: str, run_id: uuid.UUID, session: str, request: Request
) -> JSONResponse:
    svc = services(request).widgets
    widget = await svc.find(key)
    headers = _cors(request, widget)
    out = await svc.answer(widget, session, run_id)
    return JSONResponse(out.model_dump(), headers=headers)
