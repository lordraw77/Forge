"""Agentic AI Studio and chat (E1.18 to E1.20).

Agent definitions need `asset.write` (Project Admin, Builder). Chatting needs `usecase.run`;
reading runs needs `run.read` (all runs) or `run.read.own` (own runs only, enforced in the
query). Writes and runs also need a writable project. Versioned resources follow ADR-011.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from forge.agents.service import (
    AgentCreateIn,
    AgentDetail,
    AgentDuplicateIn,
    AgentPage,
    AgentPublishIn,
    AgentRunDetail,
    AgentRunOut,
    AgentRunPage,
    AgentTagIn,
    AgentUpdateIn,
    AgentUsageOut,
    AgentVersionIn,
    AgentVersionOut,
    AvailableAgent,
    ConversationDetail,
    ConversationIn,
    ConversationOut,
    FeedbackIn,
    FeedbackOut,
    MessageIn,
)
from forge.api.deps import ProjectContext, project_permission, ready_principal, services
from forge.core.errors import ProblemError

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["agents"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
Chatter = Annotated[ProjectContext, Depends(project_permission("usecase.run", writable=True))]
ChatReader = Annotated[ProjectContext, Depends(project_permission("usecase.run"))]
MovableTag = Literal["staging", "prod"]
EVENT_ID = re.compile(r"^\d{1,20}-\d{1,10}$")
KEEPALIVE_S = 15
TERMINAL = ("succeeded", "failed", "cancelled")


async def run_reader(request: Request, project_id: uuid.UUID) -> ProjectContext:
    """`run.read` sees every run, `run.read.own` only the caller's own."""
    principal = await ready_principal(request)
    access = await services(request).tenancy.access(principal.actor, project_id)
    if not ({"run.read", "run.read.own"} & access.permissions):
        raise ProblemError(403, "forbidden", "Permission 'run.read' required")
    return ProjectContext(principal=principal, access=access)


RunReader = Annotated[ProjectContext, Depends(run_reader)]


def _sees_all(ctx: ProjectContext) -> bool:
    return "run.read" in ctx.access.permissions


# ------------------------------------------------------------------ library
@router.get("/agents", response_model=AgentPage)
async def list_agents(
    ctx: Reader,
    request: Request,
    status_: Annotated[Literal["draft", "published"] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AgentPage:
    return await services(request).agents.list_agents(
        ctx.actor,
        ctx.project_id,
        status=status_,
        q=q,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )


@router.post("/agents", response_model=AgentDetail, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreateIn, ctx: Writer, request: Request) -> AgentDetail:
    return await services(request).agents.create(ctx.actor, ctx.project_id, body)


@router.get("/agents/available", response_model=list[AvailableAgent])
async def list_available_agents(ctx: ChatReader, request: Request) -> list[AvailableAgent]:
    """Agents a chat user can pick: the published ones; Builders also see drafts."""
    return await services(request).agents.available(
        ctx.actor, ctx.project_id, include_drafts="asset.write" in ctx.access.permissions
    )


@router.get("/agents/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: uuid.UUID, ctx: Reader, request: Request) -> AgentDetail:
    return await services(request).agents.get(ctx.actor, ctx.project_id, agent_id)


@router.patch("/agents/{agent_id}", response_model=AgentDetail)
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdateIn, ctx: Writer, request: Request
) -> AgentDetail:
    return await services(request).agents.update(ctx.actor, ctx.project_id, agent_id, body)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: uuid.UUID, ctx: Writer, request: Request) -> None:
    await services(request).agents.delete(ctx.actor, ctx.project_id, agent_id)


@router.post("/agents/{agent_id}/duplicate", response_model=AgentDetail, status_code=201)
async def duplicate_agent(
    agent_id: uuid.UUID, body: AgentDuplicateIn, ctx: Writer, request: Request
) -> AgentDetail:
    return await services(request).agents.duplicate(ctx.actor, ctx.project_id, agent_id, body.name)


@router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionOut])
async def list_agent_versions(
    agent_id: uuid.UUID, ctx: Reader, request: Request
) -> list[AgentVersionOut]:
    return await services(request).agents.versions(ctx.actor, ctx.project_id, agent_id)


@router.post("/agents/{agent_id}/versions", response_model=AgentDetail, status_code=201)
async def save_agent_version(
    agent_id: uuid.UUID, body: AgentVersionIn, ctx: Writer, request: Request
) -> AgentDetail:
    """Save Configuration = a new immutable version; references are pinned to versions."""
    return await services(request).agents.save_version(ctx.actor, ctx.project_id, agent_id, body)


@router.post("/agents/{agent_id}/publish", response_model=AgentDetail)
async def publish_agent(
    agent_id: uuid.UUID, body: AgentPublishIn, ctx: Writer, request: Request
) -> AgentDetail:
    return await services(request).agents.set_tag(
        ctx.actor, ctx.project_id, agent_id, "prod", body.version
    )


@router.put("/agents/{agent_id}/tags/{tag}", response_model=AgentDetail)
async def set_agent_tag(
    agent_id: uuid.UUID, tag: MovableTag, body: AgentTagIn, ctx: Writer, request: Request
) -> AgentDetail:
    return await services(request).agents.set_tag(
        ctx.actor, ctx.project_id, agent_id, tag, body.version
    )


@router.delete("/agents/{agent_id}/tags/{tag}", response_model=AgentDetail)
async def remove_agent_tag(
    agent_id: uuid.UUID, tag: MovableTag, ctx: Writer, request: Request
) -> AgentDetail:
    return await services(request).agents.remove_tag(ctx.actor, ctx.project_id, agent_id, tag)


# ------------------------------------------------------------------ chat
@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    body: ConversationIn, ctx: Chatter, request: Request
) -> ConversationOut:
    return await services(request).agents.create_conversation(
        ctx.actor, ctx.project_id, body, is_builder="asset.write" in ctx.access.permissions
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    ctx: ChatReader,
    request: Request,
    agent_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ConversationOut]:
    """The caller's own conversations."""
    return await services(request).agents.conversations(ctx.actor, ctx.project_id, agent_id, limit)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, ctx: ChatReader, request: Request
) -> ConversationDetail:
    return await services(request).agents.conversation(ctx.actor, ctx.project_id, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, ctx: Chatter, request: Request) -> None:
    await services(request).agents.delete_conversation(ctx.actor, ctx.project_id, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages", response_model=AgentRunOut, status_code=202
)
async def send_message(
    conversation_id: uuid.UUID, body: MessageIn, ctx: Chatter, request: Request
) -> AgentRunOut:
    """Starts a run (202). Follow it with `GET /runs/{id}/events`."""
    return await services(request).agents.send_message(
        ctx.actor, ctx.project_id, conversation_id, body.content
    )


# ------------------------------------------------------------------ runs
@router.get("/runs", response_model=AgentRunPage)
async def list_runs(
    ctx: RunReader,
    request: Request,
    agent_id: uuid.UUID | None = None,
    status_: Annotated[
        Literal["queued", "running", "succeeded", "failed", "cancelled"] | None,
        Query(alias="status"),
    ] = None,
    user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AgentRunPage:
    return await services(request).agents.list_runs(
        ctx.actor,
        ctx.project_id,
        can_see_all=_sees_all(ctx),
        agent_id=agent_id,
        status=status_,
        user_id=user_id,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )


@router.get("/usage", response_model=AgentUsageOut)
async def agent_usage(
    ctx: Annotated[ProjectContext, Depends(project_permission("run.read"))],
    request: Request,
    since: datetime | None = None,
    until: datetime | None = None,
) -> AgentUsageOut:
    """Daily totals of runs (tokens, cost); they equal the sums of the runs' steps."""
    return await services(request).agents.usage(ctx.actor, ctx.project_id, since=since, until=until)


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
async def get_run(run_id: uuid.UUID, ctx: RunReader, request: Request) -> AgentRunDetail:
    return await services(request).agents.get_run(
        ctx.actor, ctx.project_id, run_id, can_see_all=_sees_all(ctx)
    )


@router.post("/runs/{run_id}/cancel", response_model=AgentRunOut)
async def cancel_run(run_id: uuid.UUID, ctx: Chatter, request: Request) -> AgentRunOut:
    """The stop button: ends the run `cancelled` (own runs; Builders can stop any run)."""
    return await services(request).agents.cancel(
        ctx.actor,
        ctx.project_id,
        run_id,
        can_see_all="run.read" in ctx.access.permissions
        and "asset.write" in ctx.access.permissions,
    )


@router.put("/runs/{run_id}/feedback", response_model=FeedbackOut)
async def put_feedback(
    run_id: uuid.UUID, body: FeedbackIn, ctx: Chatter, request: Request
) -> FeedbackOut:
    return await services(request).agents.put_feedback(ctx.actor, ctx.project_id, run_id, body)


def _sse(event_id: str | None, event: dict[str, Any]) -> bytes:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n".encode()


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID,
    ctx: RunReader,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Server-sent events of a run: replay from the start (or `Last-Event-ID`), then follow.

    Event types: run_started, run_resumed, step_started, token, step_finished, citations, done,
    error. The stream ends after `done` or `error`.
    """
    svc = services(request)
    run = await svc.agents.get_run(ctx.actor, ctx.project_id, run_id, can_see_all=_sees_all(ctx))
    cursor = last_event_id if last_event_id and EVENT_ID.match(last_event_id) else "0-0"

    async def stream() -> AsyncIterator[bytes]:
        last, idle, quiet = cursor, 0, 0.0
        while True:
            if await request.is_disconnected():
                return
            entries = await svc.run_events.read(run_id, last, block_ms=1000)
            for entry_id, event in entries:
                last = entry_id
                yield _sse(entry_id, event)
                if event["type"] in ("done", "error"):
                    return
            if entries:
                idle, quiet = 0, 0.0
                continue
            idle += 1
            quiet += 1.0
            if idle >= 5:  # nothing for a while: the stream may be gone or the run over
                idle = 0
                current = await svc.agents.get_run(
                    ctx.actor, ctx.project_id, run_id, can_see_all=_sees_all(ctx)
                )
                if current.status in TERMINAL and not await svc.run_events.exists(run_id):
                    for event in await svc.agents.replay_events(current):
                        yield _sse(None, event)
                    return
                if current.status in TERMINAL:
                    # Stream exists but ended without a final event (lost publish): close it.
                    for event in (await svc.agents.replay_events(current))[-1:]:
                        yield _sse(None, event)
                    return
            if quiet >= KEEPALIVE_S:
                quiet = 0.0
                yield b": keepalive\n\n"
            await asyncio.sleep(0)

    if run.status in TERMINAL and not await svc.run_events.exists(run_id):
        events = await svc.agents.replay_events(run)

        async def replay() -> AsyncIterator[bytes]:
            for event in events:
                yield _sse(None, event)

        return StreamingResponse(replay(), media_type="text/event-stream", headers=_SSE_HEADERS)
    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


_SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
