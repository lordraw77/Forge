"""OpenAI-compatible surface (F1.3, US-1.6.x): `/v1/chat/completions`, `/v1/embeddings`,
`/v1/models`. Authenticated with a project API key holding `llm.invoke`; errors carry the
OpenAI `error` object next to the problem fields (ADR-011)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from forge.api.deps import principal, services
from forge.core.errors import ProblemError
from forge.gateway.service import Caller, GatewayResponse

router = APIRouter(prefix="/v1", tags=["openai"])
PERMISSION = "llm.invoke"


class ChatMessage(BaseModel):
    # Extra fields (e.g. `reasoning_content` of a previous turn) pass through unchanged.
    model_config = ConfigDict(extra="allow")
    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=2000)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    n: Literal[1] | None = None
    stop: str | list[str] | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    logit_bias: dict[str, float] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = Field(default=None, ge=0, le=20)
    seed: int | None = None
    user: str | None = Field(default=None, max_length=256)
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=128)
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    response_format: dict[str, Any] | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None


class EmbeddingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    input: str | list[str]
    encoding_format: Literal["float", "base64"] | None = None
    dimensions: int | None = Field(default=None, ge=1, le=4000)
    user: str | None = Field(default=None, max_length=256)


class ModelEntry(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelEntry]


async def llm_caller(request: Request) -> Caller:
    p = await principal(request)
    if p.kind != "api_key" or p.actor.api_key_project is None:
        raise ProblemError(
            401, "api_key_required", "Use a project API key: Authorization: Bearer fk_…"
        )
    access = await services(request).tenancy.access(p.actor, p.actor.api_key_project)
    if PERMISSION not in access.permissions:
        raise ProblemError(403, "forbidden", f"Permission '{PERMISSION}' required")
    if access.project.status != "active":
        raise ProblemError(409, "project_archived", "Project is archived (read-only)")
    key_id = p.actor.label.removeprefix("api_key:")
    return Caller(
        project_id=access.project.id,
        user_id=p.actor.id,
        api_key_id=uuid.UUID(key_id) if key_id else None,
        trace_id=request.headers.get("x-request-id"),
    )


LlmCaller = Annotated[Caller, Depends(llm_caller)]


def _respond(result: GatewayResponse) -> JSONResponse | StreamingResponse:
    if result.stream is not None:
        return StreamingResponse(
            result.stream,
            media_type="text/event-stream",
            headers=result.headers | {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(result.body, headers=result.headers)


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionIn, caller: LlmCaller, request: Request
) -> JSONResponse | StreamingResponse:
    params = body.model_dump(exclude_none=True, exclude={"model"})
    params["messages"] = [m.model_dump(exclude_none=True) for m in body.messages]
    result = await services(request).gateway.service.chat(caller, body.model, params)
    return _respond(result)


@router.post("/embeddings", response_model=None)
async def embeddings(body: EmbeddingsIn, caller: LlmCaller, request: Request) -> JSONResponse:
    inputs = [body.input] if isinstance(body.input, str) else body.input
    params = body.model_dump(exclude_none=True, exclude={"model", "input", "user"})
    result = await services(request).gateway.service.embeddings(caller, body.model, inputs, params)
    return JSONResponse(result.body, headers=result.headers)


@router.get("/models", response_model=ModelList)
async def list_models(caller: LlmCaller, request: Request) -> ModelList:
    models, aliases = services(request).gateway.registry.available(caller.project_id)
    return ModelList(
        data=[ModelEntry(id=a, owned_by="forge-alias") for a in aliases]
        + [ModelEntry(id=m.name, owned_by=m.provider.name) for m in models]
    )
