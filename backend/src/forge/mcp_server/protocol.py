"""The project's MCP server (E3.5): JSON-RPC over Streamable HTTP, answered statelessly.

It speaks the handshake era of the protocol (`initialize`, versions 2024-11-05 to 2025-11-25) and
refuses `server/discover`, so current clients fall back to `initialize`. No session id is issued:
every POST is complete in itself and carries the caller's API key, which lets any replica answer.
What is offered is the project's *exposure list*: agents (a tool that asks the agent),
data collections (a tool that searches) and prompts (MCP prompts, rendered with arguments).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge import __version__
from forge.agents.schemas import ConversationIn
from forge.agents.studio import AgentStudio
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.logging import get_logger
from forge.gateway.service import Caller
from forge.mcp_server.models import McpExposure
from forge.prompts.service import PromptStudio
from forge.prompts.templates import TemplateError, render
from forge.rag.studio import RagStudio

log = get_logger(__name__)

SUPPORTED_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)
TERMINAL = ("succeeded", "failed", "cancelled")
ASK_TIMEOUT_S = 170
POLL_S = 0.5
MAX_MESSAGE_CHARS = 20_000
INSTRUCTIONS = (
    "Tools and prompts of one Forge project. Agent tools take a `message` and answer with the "
    "agent's reply; pass back the returned `conversation_id` to continue the conversation. "
    "Collection tools search the project's documents."
)


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def reply(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def text_result(text: str, *, error: bool = False, **structured: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if structured:
        out["structuredContent"] = structured
    return out


class McpEndpoint:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentStudio,
        rag: RagStudio,
        prompts: PromptStudio,
    ) -> None:
        self._sessions = sessions
        self._agents = agents
        self._rag = rag
        self._prompts = prompts

    # ------------------------------------------------------------------ entry
    async def handle(
        self, actor: Actor, project_id: uuid.UUID, payload: Any
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """One message or a batch. None when nothing needs an answer (notifications)."""
        if isinstance(payload, list):
            if not payload:
                return failure(None, INVALID_REQUEST, "Empty batch")
            answers = [a for m in payload if (a := await self._one(actor, project_id, m))]
            return answers or None
        return await self._one(actor, project_id, payload)

    async def _one(
        self, actor: Actor, project_id: uuid.UUID, message: Any
    ) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return failure(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message")
        method, request_id = message.get("method"), message.get("id")
        if not isinstance(method, str):
            return None  # a response to something we never asked: ignore
        is_notification = "id" not in message
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else failure(request_id, INVALID_PARAMS, "Bad params")
        try:
            result = await self._dispatch(actor, project_id, method, params)
        except RpcError as exc:
            return None if is_notification else failure(request_id, exc.code, exc.message, exc.data)
        except ProblemError as exc:
            if is_notification:
                return None
            return failure(request_id, INVALID_PARAMS, exc.title)
        except Exception:
            log.exception("mcp.internal_error", method=method)
            return None if is_notification else failure(request_id, INTERNAL, "Internal error")
        return None if is_notification else reply(request_id, result)

    async def _dispatch(
        self, actor: Actor, project_id: uuid.UUID, method: str, params: dict[str, Any]
    ) -> Any:
        if method == "initialize":
            wanted = str(params.get("protocolVersion") or "")
            return {
                "protocolVersion": wanted
                if wanted in SUPPORTED_VERSIONS
                else SUPPORTED_VERSIONS[-1],
                "capabilities": {
                    "tools": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "forge", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        if method == "ping":
            return {}
        if method.startswith("notifications/"):
            return None
        if method == "tools/list":
            return {"tools": await self._tools(actor, project_id)}
        if method == "tools/call":
            return await self._call(actor, project_id, params)
        if method == "prompts/list":
            return {"prompts": await self._prompt_list(actor, project_id)}
        if method == "prompts/get":
            return await self._prompt_get(actor, project_id, params)
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    # ------------------------------------------------------------------ what is offered
    async def _exposed(self, actor: Actor, project_id: uuid.UUID) -> list[McpExposure]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            rows = (
                (
                    await s.execute(
                        select(McpExposure).where(McpExposure.enabled).order_by(McpExposure.name)
                    )
                )
                .scalars()
                .all()
            )
            for r in rows:
                s.expunge(r)
            return list(rows)

    async def _tools(self, actor: Actor, project_id: uuid.UUID) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for e in await self._exposed(actor, project_id):
            if e.agent_id:
                tools.append(
                    {
                        "name": e.name,
                        "description": e.description or "Ask the agent",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "What to ask"},
                                "conversation_id": {
                                    "type": "string",
                                    "description": "Continue an earlier conversation",
                                },
                            },
                            "required": ["message"],
                        },
                    }
                )
            elif e.collection_id:
                tools.append(
                    {
                        "name": e.name,
                        "description": e.description or "Search the documents of a collection",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                            },
                            "required": ["query"],
                        },
                    }
                )
        return tools

    # ------------------------------------------------------------------ tools/call
    async def _call(self, actor: Actor, project_id: uuid.UUID, params: dict[str, Any]) -> Any:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "tools/call needs a name and an arguments object")
        exposure = next(
            (
                e
                for e in await self._exposed(actor, project_id)
                if e.name == name and not e.prompt_id
            ),
            None,
        )
        if exposure is None:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            if exposure.agent_id:
                return await self._ask(actor, project_id, exposure.agent_id, args)
            assert exposure.collection_id
            return await self._search(actor, project_id, exposure.collection_id, args)
        except ProblemError as exc:
            return text_result(f"{exc.title}", error=True)

    async def _ask(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID, args: dict[str, Any]
    ) -> dict[str, Any]:
        message = args.get("message")
        if not isinstance(message, str) or not message.strip():
            raise RpcError(INVALID_PARAMS, "`message` is required")
        message = message[:MAX_MESSAGE_CHARS]
        raw = args.get("conversation_id")
        if raw:
            try:
                conversation_id = uuid.UUID(str(raw))
            except ValueError as exc:
                raise RpcError(INVALID_PARAMS, "`conversation_id` is not an id") from exc
        else:
            conversation_id = (
                await self._agents.create_conversation(
                    actor,
                    project_id,
                    ConversationIn(agent_id=agent_id),
                    is_builder=False,
                    source="mcp",
                )
            ).id
        run = await self._agents.send_message(actor, project_id, conversation_id, message)
        deadline = time.monotonic() + ASK_TIMEOUT_S
        while time.monotonic() < deadline:
            current = await self._agents.get_run(actor, project_id, run.id, can_see_all=False)
            if current.status in TERMINAL:
                if current.status != "succeeded":
                    return text_result(
                        f"The agent could not answer: {current.error or current.status}",
                        error=True,
                        conversation_id=str(conversation_id),
                    )
                return text_result(
                    current.output or "",
                    conversation_id=str(conversation_id),
                    run_id=str(run.id),
                )
            await asyncio.sleep(POLL_S)
        return text_result(
            "The agent is still working; ask again with the same conversation_id in a moment.",
            error=True,
            conversation_id=str(conversation_id),
            run_id=str(run.id),
        )

    async def _search(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID, args: dict[str, Any]
    ) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise RpcError(INVALID_PARAMS, "`query` is required")
        top_k = args.get("top_k", 5)
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 10:
            raise RpcError(INVALID_PARAMS, "`top_k` is 1 to 10")
        # API keys read project-wide documents only (ADR-003); a session user keeps their groups.
        user = None if actor.api_key_project else actor.id
        caller = Caller(project_id, user, trace_id=f"mcp:{uuid.uuid4().hex}")
        found = await self._rag.collection_search(caller, collection_id, query[:2000], top_k)
        if not found.passages:
            return text_result("No passage matched.", passages=[])
        lines = []
        for i, p in enumerate(found.passages, start=1):
            where = f"{p.document_name}" + (f", page {p.page}" if p.page else "")
            lines.append(f"[{i}] {where}\n{p.content}")
        return text_result(
            "\n\n".join(lines),
            passages=[
                {
                    "document": p.document_name,
                    "page": p.page,
                    "heading": p.heading_path,
                    "score": p.score,
                    "content": p.content,
                }
                for p in found.passages
            ],
        )

    # ------------------------------------------------------------------ prompts
    async def _prompt_exposures(self, actor: Actor, project_id: uuid.UUID) -> list[McpExposure]:
        return [e for e in await self._exposed(actor, project_id) if e.prompt_id]

    async def _prompt_list(self, actor: Actor, project_id: uuid.UUID) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for e in await self._prompt_exposures(actor, project_id):
            assert e.prompt_id
            detail = await self._prompts.get(actor, project_id, e.prompt_id)
            out.append(
                {
                    "name": e.name,
                    "description": e.description,
                    "arguments": [
                        {"name": v.name, "required": v.required and not v.value}
                        for v in detail.version.variables
                    ],
                }
            )
        return out

    async def _prompt_get(
        self, actor: Actor, project_id: uuid.UUID, params: dict[str, Any]
    ) -> dict[str, Any]:
        name, args = params.get("name"), params.get("arguments") or {}
        exposure = next(
            (e for e in await self._prompt_exposures(actor, project_id) if e.name == name), None
        )
        if exposure is None or not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, f"Unknown prompt: {name}")
        assert exposure.prompt_id
        version = (await self._prompts.get(actor, project_id, exposure.prompt_id)).version
        values = {v.name: v.value for v in version.variables} | {
            str(k): str(v) for k, v in args.items()
        }
        missing = [v.name for v in version.variables if v.required and not values.get(v.name)]
        if missing:
            raise RpcError(INVALID_PARAMS, f"Missing arguments: {', '.join(missing)}")
        try:
            system = await asyncio.to_thread(render, version.system_template, values)
            user = await asyncio.to_thread(render, version.user_template, values)
        except TemplateError as exc:
            raise RpcError(
                INVALID_PARAMS, f"The prompt could not be rendered: {exc.message}"
            ) from exc
        # MCP prompt messages are user or assistant; the system text leads the first message.
        body = "\n\n".join(t for t in (system.strip(), user.strip()) if t)
        return {
            "description": exposure.description,
            "messages": [{"role": "user", "content": {"type": "text", "text": body}}],
        }
