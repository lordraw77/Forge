"""Worker side of use case runs: begin, one activity per node, finish. Node work reuses the
agent runtime (agents), the tool library (tools) and the prompt studio (prompts) through their
service interfaces."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from decimal import Decimal
from typing import Any

import jsonschema
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from forge.agents.service import AgentRuntime
from forge.core import egress
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.logging import get_logger
from forge.core.models import utcnow
from forge.prompts.service import PromptStudio, RunIn
from forge.tools.service import ToolStudio
from forge.usecases.files import UseCaseFiles
from forge.usecases.models import UseCaseRun, UseCaseRunStep
from forge.usecases.workflow import (
    BEGIN_ACTIVITY,
    FINISH_ACTIVITY,
    NODE_ACTIVITY,
    NOTIFY_ACTIVITY,
    NOTIFY_ATTEMPTS,
    UseCaseRunWorkflow,
)

log = get_logger(__name__)
WORKFLOWS = [UseCaseRunWorkflow]
HEARTBEAT_EVERY_S = 2
REF = re.compile(r"^(.+)@(\d+)$")
OUTPUT_BYTES = 64 * 1024


def _json(value: Any) -> Any:
    raw = json.dumps(value, default=str, ensure_ascii=False)
    if len(raw.encode()) <= OUTPUT_BYTES:
        return json.loads(raw)
    return {"truncated": True, "preview": raw[:OUTPUT_BYTES]}


def _split(ref: str) -> tuple[str, int]:
    m = REF.match(ref)
    if not m:
        raise ProblemError(422, "bad_reference", f"'{ref}' is not name@version")
    return m.group(1), int(m.group(2))


class UseCaseRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentRuntime,
        tools: ToolStudio,
        prompts: PromptStudio,
        files: UseCaseFiles,
    ) -> None:
        self._sessions = sessions
        self._agents = agents
        self._tools = tools
        self._prompts = prompts
        self._files = files

    # ------------------------------------------------------------------ begin / finish
    async def begin(self, project_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is None or run.status not in ("queued", "running"):
                return {"skipped": True}
            if run.status == "queued":
                run.status, run.started_at = "running", utcnow()
            graph, inputs, limits = run.graph, dict(run.inputs), run.limits
        names = [n for n in graph.get("_files", []) if inputs.get(n)]
        if names:  # file fields reach the graph as the text extracted at upload
            texts = await self._files.file_texts(
                project_id, [uuid.UUID(str(inputs[n])) for n in names]
            )
            for n in names:
                inputs[n] = texts.get(str(inputs[n]), "")
        return {"graph": graph, "inputs": inputs, "limits": limits}

    async def finish(
        self, project_id: uuid.UUID, run_id: uuid.UUID, result: dict[str, Any]
    ) -> None:
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is None or run.status not in ("queued", "running"):
                return
            status, code, message = (
                str(result["status"]),
                result.get("error_code"),
                result.get("error"),
            )
            outputs: dict[str, Any] | None = None
            if status == "succeeded":
                value, problem = self._deliverable(run, result.get("output"))
                if problem:
                    status, code, message = "failed", "output_invalid", problem
                else:
                    outputs = _json(
                        {
                            "result": value,
                            "skipped": result.get("skipped", []),
                            "decisions": result.get("decisions", []),
                        }
                    )
            kind = (run.graph.get("_output") or {}).get("type")
            created_by, name = run.created_by, run.use_case_name
        if outputs is None and status != "queued":
            outputs = _json(
                {"skipped": result.get("skipped", []), "decisions": result.get("decisions", [])}
            )
        # The file is stored before the status changes: a reader who sees "succeeded" finds it.
        wants_file = status == "succeeded" and kind in ("docx", "xlsx", "pdf")
        if wants_file and outputs is not None:
            problem_text = await self._store_file(
                project_id, run_id, created_by, str(kind), name, outputs
            )
            if problem_text:
                outputs = {**outputs, "file_error": problem_text}
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is None or run.status not in ("queued", "running"):
                return
            run.status = status
            run.outputs = outputs
            run.error_code = str(code) if code else None
            run.error = str(message)[:500] if message else None
            run.error_node = result.get("error_node")
            run.steps = int(result.get("steps", 0))
            run.cost = Decimal(str(result.get("cost", 0)))
            run.tokens_in = int(result.get("tokens_in", 0))
            run.tokens_out = int(result.get("tokens_out", 0))
            run.finished_at = utcnow()
            if status != "succeeded":
                await s.execute(
                    update(UseCaseRunStep)
                    .where(UseCaseRunStep.run_id == run_id, UseCaseRunStep.status == "running")
                    .values(status="failed", error_code="run_ended", finished_at=utcnow())
                )
            if run.webhook_url:
                run.webhook_status = "pending"

    async def _store_file(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        created_by: uuid.UUID | None,
        kind: str,
        title: str,
        outputs: dict[str, Any],
    ) -> str | None:
        """The DOCX, XLSX or PDF promised by the use case. A failure leaves the run succeeded
        (the text output is intact) and is reported in the outputs."""
        try:
            await self._files.store_output(
                project_id, run_id, created_by, kind, title, outputs.get("result")
            )
        except Exception as exc:
            log.warning("usecase.output_file_failed", run=str(run_id), error=str(exc)[:200])
            return f"The {kind} file could not be created"
        return None

    async def notify(self, project_id: uuid.UUID, run_id: uuid.UUID, attempt: int) -> None:
        """Call the webhook of an asynchronous run (raises to be retried; the last attempt
        records the failure). The body names the run and its status, never the output: the
        receiver fetches the result with its own API key."""
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is None or not run.webhook_url or run.webhook_status == "delivered":
                return
            url = run.webhook_url
            body = {
                "event": "use_case_run.finished",
                "run_id": str(run.id),
                "use_case_id": str(run.use_case_id),
                "use_case_name": run.use_case_name,
                "version": run.version,
                "status": run.status,
                "error_code": run.error_code,
                "cost": float(run.cost),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }
            actor = Actor(id=run.created_by, label="use_case_run")
        problem: str | None = None
        try:
            hosts, allow_private = await self._tools.get_policy(actor, project_id)
            res = await egress.request(
                "POST", url, hosts, allow_private=allow_private, json_body=body, timeout_s=10
            )
            if not 200 <= res.status < 300:
                problem = f"The receiver answered HTTP {res.status}"
        except egress.EgressError as exc:
            problem = exc.message
        last = attempt >= NOTIFY_ATTEMPTS
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is not None:
                if problem is None:
                    run.webhook_status, run.webhook_error = "delivered", None
                elif last:
                    run.webhook_status, run.webhook_error = "failed", problem[:300]
        if problem is not None and not last:
            raise RuntimeError(problem)

    @staticmethod
    def _deliverable(run: UseCaseRun, value: Any) -> tuple[Any, str | None]:
        """The final output in the shape the use case promises (JSON outputs are checked
        against their schema)."""
        schema = (run.graph.get("_output") or {}).get("schema")
        if (run.graph.get("_output") or {}).get("type") != "json":
            return value, None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value, "The final output is not valid JSON"
        if schema:
            errors = list(jsonschema.Draft202012Validator(schema).iter_errors(value))
            if errors:
                return value, "The final output does not match the schema: " + errors[0].message
        return value, None

    # ------------------------------------------------------------------ nodes
    async def run_node(self, payload: dict[str, Any], attempt: int) -> dict[str, Any]:
        project_id, run_id = uuid.UUID(payload["project_id"]), uuid.UUID(payload["run_id"])
        key = str(payload["key"])
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is None:
                return {"error_code": "run_missing", "error": "The run no longer exists"}
            user_id = run.created_by
            step = await s.get(UseCaseRunStep, (run_id, key))
            if step is not None and step.status == "succeeded":
                out = step.output or {}
                return {
                    "output": out.get("value"),
                    "cost": float(step.cost),
                    "tokens_in": step.tokens_in,
                    "tokens_out": step.tokens_out,
                }
            if step is None:
                step = UseCaseRunStep(
                    run_id=run_id,
                    step_key=key,
                    project_id=project_id,
                    node_id=str(payload["node"]),
                    node_type=str(payload["kind"]),
                    ref=str(payload.get("ref")),
                    status="running",
                    input=_json({k: payload.get(k) for k in ("message", "args", "variables")}),
                )
                s.add(step)
            else:
                step.status, step.error_code, step.error = "running", None, None
            agent_run_id = step.agent_run_id
        started = time.monotonic()
        result: dict[str, Any]
        try:
            result = await self._dispatch(
                payload, project_id, run_id, user_id, agent_run_id, attempt
            )
        except asyncio.CancelledError:
            await self._cancel_agent(project_id, run_id, key)
            raise
        except ProblemError as exc:
            result = {"error_code": exc.code, "error": exc.title}
        latency = int((time.monotonic() - started) * 1000)
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(UseCaseRunStep, (run_id, key))
            if row is not None:
                row.finished_at, row.latency_ms = utcnow(), latency
                if result.get("error_code"):
                    row.status = "failed"
                    row.error_code = str(result["error_code"])
                    row.error = str(result.get("error", ""))[:500]
                else:
                    row.status = "succeeded"
                    row.output = _json({"value": result.get("output")})
                row.cost = Decimal(str(result.get("cost", 0)))
                row.tokens_in = int(result.get("tokens_in", 0))
                row.tokens_out = int(result.get("tokens_out", 0))
        return result

    async def _cancel_agent(self, project_id: uuid.UUID, run_id: uuid.UUID, key: str) -> None:
        with contextlib.suppress(Exception):
            async with project_scope(self._sessions, project_id) as s:
                step = await s.get(UseCaseRunStep, (run_id, key))
                agent_run = step.agent_run_id if step is not None else None
            if agent_run:
                await self._agents.request_cancel(project_id, agent_run)

    async def _dispatch(
        self,
        payload: dict[str, Any],
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID | None,
        agent_run_id: uuid.UUID | None,
        attempt: int,
    ) -> dict[str, Any]:
        kind = payload["kind"]
        name, version = _split(str(payload["ref"]))
        if kind == "agent":
            return await self._agent(
                payload, project_id, run_id, user_id, agent_run_id, attempt, name, version
            )
        if kind == "tool":
            return await self._tool(payload, project_id, run_id, user_id, name, version)
        return await self._llm(payload, project_id, user_id, name, version)

    async def _agent(
        self,
        payload: dict[str, Any],
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID | None,
        agent_run_id: uuid.UUID | None,
        attempt: int,
        name: str,
        version: int,
    ) -> dict[str, Any]:
        if agent_run_id is None:
            agent_run_id = await self._agents.create_run(
                user_id,
                project_id,
                name,
                version,
                str(payload.get("message") or " "),
                title=f"[use case] {payload['node']}",
            )
            async with project_scope(self._sessions, project_id) as s:
                await s.execute(
                    update(UseCaseRunStep)
                    .where(
                        UseCaseRunStep.run_id == run_id,
                        UseCaseRunStep.step_key == payload["key"],
                    )
                    .values(agent_run_id=agent_run_id)
                )
        await self._agents.execute(project_id, agent_run_id, attempt)
        out = await self._agents.outcome(project_id, agent_run_id)
        usage = {
            "cost": out["cost"],
            "tokens_in": out["tokens_in"],
            "tokens_out": out["tokens_out"],
        }
        if out["status"] != "succeeded":
            return {
                "error_code": f"agent_{out['error_code'] or out['status']}",
                "error": out["error"] or f"The agent run {out['status']}",
                **usage,
            }
        return {"output": (out["output"] or "").strip(), **usage}

    async def _tool(
        self,
        payload: dict[str, Any],
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID | None,
        name: str,
        version: int,
    ) -> dict[str, Any]:
        prepared = await self._tools.prepare_named(user_id, project_id, name, version)
        if prepared.requires_approval:
            return {
                "error_code": "approval_not_available",
                "error": "Human approval is not available yet",
            }
        args = payload.get("args") or {}
        res = await prepared.execute(args)
        await self._tools.log_call(
            project_id, user_id, prepared, args, res, source="agent", run_id=run_id
        )
        if res.status != "ok":
            return {"error_code": f"tool_{res.error_code or 'error'}", "error": res.content[:500]}
        return {"output": res.content}

    async def _llm(
        self,
        payload: dict[str, Any],
        project_id: uuid.UUID,
        user_id: uuid.UUID | None,
        name: str,
        version: int,
    ) -> dict[str, Any]:
        actor = Actor(id=user_id, label="use_case_run")
        prompt_id = await self._prompts.find_prompt(actor, project_id, name)
        if prompt_id is None:
            return {"error_code": "unknown_prompt", "error": f"Prompt '{name}' does not exist"}
        run = await self._prompts.test(
            actor,
            project_id,
            prompt_id,
            RunIn(version=version, variables=payload.get("variables") or {}),
        )
        usage = {
            "cost": float(run.cost),
            "tokens_in": run.usage.tokens_in,
            "tokens_out": run.usage.tokens_out,
        }
        if run.status != "ok":
            return {
                "error_code": f"llm_{run.error_code or 'error'}",
                "error": run.error or "",
                **usage,
            }
        return {"output": run.output or "", **usage}


class UseCaseActivities:
    def __init__(self, runtime: UseCaseRuntime) -> None:
        self._rt = runtime

    def all(self) -> list[Any]:
        return [self.begin, self.run_node, self.finish, self.notify]

    @activity.defn(name=BEGIN_ACTIVITY)
    async def begin(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._rt.begin(uuid.UUID(payload["project_id"]), uuid.UUID(payload["run_id"]))

    @activity.defn(name=NODE_ACTIVITY)
    async def run_node(self, payload: dict[str, Any]) -> dict[str, Any]:
        async def beat() -> None:
            while True:
                activity.heartbeat()
                await asyncio.sleep(HEARTBEAT_EVERY_S)

        heartbeat = asyncio.create_task(beat())
        try:
            return await self._rt.run_node(payload, activity.info().attempt)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    @activity.defn(name=FINISH_ACTIVITY)
    async def finish(self, payload: dict[str, Any]) -> None:
        await self._rt.finish(
            uuid.UUID(payload["project_id"]), uuid.UUID(payload["run_id"]), payload["result"]
        )

    @activity.defn(name=NOTIFY_ACTIVITY)
    async def notify(self, payload: dict[str, Any]) -> None:
        await self._rt.notify(
            uuid.UUID(payload["project_id"]),
            uuid.UUID(payload["run_id"]),
            activity.info().attempt,
        )
