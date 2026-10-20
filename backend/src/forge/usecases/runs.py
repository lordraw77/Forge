"""Starting, reading and cancelling use case runs (API side of M2.3b)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jsonschema
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.agents.service import RunStarter
from forge.core import egress
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.observability.service import current_trace_id
from forge.tools.service import ToolStudio
from forge.usecases import definition as defs
from forge.usecases import graph as g
from forge.usecases.files import UseCaseFiles
from forge.usecases.models import (
    UseCase,
    UseCaseRun,
    UseCaseRunStep,
    UseCaseSession,
    UseCaseVersion,
)
from forge.usecases.schemas import (
    RunStartIn,
    RunStepOut,
    SessionDetail,
    SessionOut,
    UseCaseReferenceOut,
    UseCaseRunDetail,
    UseCaseRunOut,
)
from forge.usecases.service import NOT_FOUND, UseCaseStudio

RUN_WORKFLOW = "UseCaseRunWorkflow"
TERMINAL = ("succeeded", "failed", "cancelled")
RUN_NOT_FOUND = ProblemError(404, "not_found", "Run not found")
SESSION_NOT_FOUND = ProblemError(404, "not_found", "Session not found")
WATCH_POLL_S = 1.0
WATCH_MAX_S = 3 * 3600


class UseCaseRuns:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        studio: UseCaseStudio,
        starter: RunStarter,
        tools: ToolStudio,
        files: UseCaseFiles,
    ) -> None:
        self._sessions = sessions
        self._studio = studio
        self._starter = starter
        self._tools = tools
        self.files = files
        studio.add_run_check(self.runs_of)

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    @staticmethod
    async def runs_of(s: AsyncSession, use_case_id: uuid.UUID) -> list[UseCaseReferenceOut]:
        n = (
            await s.execute(
                select(func.count())
                .select_from(UseCaseRun)
                .where(UseCaseRun.use_case_id == use_case_id)
            )
        ).scalar_one()
        return [UseCaseReferenceOut(kind="run", id=use_case_id, name=f"{n} runs")] if n else []

    # ------------------------------------------------------------------ start
    @staticmethod
    def _check_inputs(schema: dict[str, Any], inputs: dict[str, Any]) -> None:
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(inputs), key=lambda e: list(e.path)
        )
        if errors:
            raise ProblemError(
                422,
                "invalid_input",
                "The inputs do not match the use case form",
                [
                    {"message": (".".join(map(str, e.path)) + ": " if e.path else "") + e.message}
                    for e in errors[:10]
                ],
            )

    async def _webhook_ok(self, actor: Actor, project_id: uuid.UUID, url: str) -> None:
        hosts, allow_private = await self._tools.get_policy(actor, project_id)
        try:
            await egress.check_url(url, hosts, allow_private)
        except egress.EgressError as exc:
            raise ProblemError(
                422,
                "invalid_webhook",
                f"The webhook URL is not allowed: {exc.message}",
                [{"message": "Hosts are allowed in the project's egress policy (Tools Studio)"}],
            ) from exc

    async def start(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        use_case_id: uuid.UUID,
        body: RunStartIn,
        *,
        can_build: bool,
        inspect: bool | None = None,
    ) -> UseCaseRunDetail:
        inspect = can_build if inspect is None else inspect
        if body.mode == "test" and not can_build:
            raise ProblemError(403, "forbidden", "Test runs need permission 'usecase.build'")
        if body.webhook_url:
            await self._webhook_ok(actor, project_id, body.webhook_url)
        async with self._session(actor, project_id) as s:
            uc = (
                await s.execute(select(UseCase).where(UseCase.id == use_case_id))
            ).scalar_one_or_none()
            if uc is None:
                raise NOT_FOUND
            if body.mode == "test":
                issues = await self._studio.readiness(actor, project_id, uc)
                if issues:
                    raise ProblemError(
                        422,
                        "not_ready",
                        "The draft is not ready to run",
                        [i.as_dict() for i in issues],
                    )
                snap: Any = uc
                version: int | None = None
            else:
                if uc.status != "published" or uc.published_version is None:
                    raise NOT_FOUND
                snap = (
                    await s.execute(
                        select(UseCaseVersion).where(
                            UseCaseVersion.use_case_id == uc.id,
                            UseCaseVersion.version == uc.published_version,
                        )
                    )
                ).scalar_one()
                version = snap.version
        if body.mode == "published":
            # Same rule as the catalog: a use case the caller may not see does not exist.
            await self._studio.catalog_item(actor, project_id, use_case_id, see_all=can_build)
        schema = (
            snap.input_schema if body.mode == "published" else defs.input_schema(snap.input_fields)
        )
        self._check_inputs(schema, body.inputs)
        names = [str(f["name"]) for f in snap.input_fields]
        file_fields = [str(f["name"]) for f in snap.input_fields if f.get("type") == "file"]
        graph = dict(g.executable_graph(snap.pattern, snap.definition, names))
        graph["_output"] = {"type": snap.output_type, "schema": snap.output_schema}
        graph["_files"] = file_fields
        limits = g.limits_of(graph)
        defaults = snap.defaults or {}
        limits["max_cost_usd"] = min(
            limits["max_cost_usd"], float(defaults.get("max_cost_usd", 5.0))
        )
        limits["timeout_s"] = min(limits["timeout_s"], int(defaults.get("max_duration_s", 1800)))
        async with self._session(actor, project_id) as s:
            await self.files.check_inputs(s, actor, file_fields, body.inputs)
            session_id = await self._session_for(s, actor, project_id, uc, body, snap.name)
            run = UseCaseRun(
                project_id=project_id,
                use_case_id=use_case_id,
                use_case_name=snap.name if hasattr(snap, "name") else uc.name,
                version=version,
                mode=body.mode,
                graph=graph,
                inputs=body.inputs,
                limits=limits,
                trace_id=current_trace_id() or uuid.uuid4().hex,
                created_by=actor.id,
                session_id=session_id,
                webhook_url=body.webhook_url,
                webhook_status="pending" if body.webhook_url else None,
            )
            s.add(run)
            await s.flush()
            run.workflow_id = f"uc-run-{run.id}"
            await audit.record(
                s,
                actor,
                "usecase.run",
                "use_case",
                use_case_id,
                {"run": str(run.id), "mode": body.mode, "version": version},
                project_id=project_id,
            )
            run_id, workflow_id = run.id, run.workflow_id
        payload: dict[str, object] = {"project_id": str(project_id), "run_id": str(run_id)}
        if not await self._starter.start_named(RUN_WORKFLOW, payload, workflow_id):
            async with self._session(actor, project_id) as s:
                failed = await s.get(UseCaseRun, run_id)
                assert failed is not None
                failed.status, failed.error_code = "failed", "queue_unavailable"
                failed.error = "The run could not be queued; try again in a moment"
                failed.finished_at = utcnow()
            raise ProblemError(
                503, "queue_unavailable", "The run could not be queued; try again in a moment"
            )
        return await self.get(actor, project_id, run_id, see_all=True, inspect=inspect)

    async def _session_for(
        self,
        s: AsyncSession,
        actor: Actor,
        project_id: uuid.UUID,
        uc: UseCase,
        body: RunStartIn,
        name: str,
    ) -> uuid.UUID | None:
        """The session a published run belongs to: the one named, or a new one. Test runs of
        builders have no session."""
        if body.mode == "test":
            return None
        if body.session_id is not None:
            sess = (
                await s.execute(select(UseCaseSession).where(UseCaseSession.id == body.session_id))
            ).scalar_one_or_none()
            if sess is None or sess.created_by != actor.id or sess.use_case_id != uc.id:
                raise SESSION_NOT_FOUND
            sess.updated_at = utcnow()
            return sess.id
        first = next((v for v in body.inputs.values() if isinstance(v, str) and v.strip()), "")
        title = (first.strip().splitlines()[0][:80] if first and len(first) < 2000 else "") or name
        sess = UseCaseSession(
            project_id=project_id, use_case_id=uc.id, title=title, created_by=actor.id
        )
        s.add(sess)
        await s.flush()
        return sess.id

    async def rerun(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        see_all: bool,
        can_build: bool,
        inspect: bool,
    ) -> UseCaseRunDetail:
        """The same inputs on the version live now, in the same session (US-2.9.2 #4)."""
        async with self._session(actor, project_id) as s:
            old = await self._visible(s, actor, run_id, see_all=see_all)
            body = RunStartIn(
                inputs=dict(old.inputs),
                mode="test" if old.mode == "test" else "published",
                session_id=old.session_id if old.created_by == actor.id else None,
            )
            use_case_id = old.use_case_id
        return await self.start(
            actor, project_id, use_case_id, body, can_build=can_build, inspect=inspect
        )

    # ------------------------------------------------------------------ read
    @staticmethod
    def _out(run: UseCaseRun) -> UseCaseRunOut:
        return UseCaseRunOut.model_validate({**run.__dict__, "cost": float(run.cost)})

    async def _visible(
        self, s: AsyncSession, actor: Actor, run_id: uuid.UUID, *, see_all: bool
    ) -> UseCaseRun:
        run = (
            await s.execute(select(UseCaseRun).where(UseCaseRun.id == run_id))
        ).scalar_one_or_none()
        if run is None or (not see_all and run.created_by != actor.id):
            raise RUN_NOT_FOUND
        return run

    async def get(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        see_all: bool,
        inspect: bool = True,
    ) -> UseCaseRunDetail:
        """`inspect` (builders, operators, admins: permission run.read) adds the graph and the
        content of every step; end users get the progress, the result and the files only."""
        async with self._session(actor, project_id) as s:
            run = await self._visible(s, actor, run_id, see_all=see_all)
            steps = (
                (
                    await s.execute(
                        select(UseCaseRunStep)
                        .where(UseCaseRunStep.run_id == run.id)
                        .order_by(UseCaseRunStep.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            files = await self.files.files_of(s, run.id)
            step_list = [
                RunStepOut.model_validate({**st.__dict__, "cost": float(st.cost)}) for st in steps
            ]
            outputs, limits = run.outputs, run.limits
            graph: dict[str, Any] | None = {
                k: v for k, v in run.graph.items() if not k.startswith("_")
            }
            if not inspect:
                graph, limits = None, {}
                outputs = (
                    {k: v for k, v in run.outputs.items() if k in ("result", "file_error")}
                    if run.outputs
                    else None
                )
                step_list = [
                    st.model_copy(
                        update={
                            "step_key": st.node_id
                            if st.node_type != "human_approval"
                            else f"{st.node_id}:approval",
                            "agent_run_id": None,
                            "ref": None,
                            "input": None,
                            "output": None,
                            "error": None,
                            "error_code": None,
                        }
                    )
                    for st in step_list
                ]
            return UseCaseRunDetail(
                **self._out(run).model_dump(),
                inputs=run.inputs,
                outputs=outputs,
                limits=limits,
                graph=graph,
                step_list=step_list,
                files=files,
                webhook_url=run.webhook_url,
                webhook_status=run.webhook_status,
                webhook_error=run.webhook_error,
            )

    async def list_runs(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        see_all: bool,
        use_case_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[UseCaseRunOut]:
        stmt = (
            select(UseCaseRun).order_by(UseCaseRun.created_at.desc()).limit(max(1, min(limit, 200)))
        )
        if not see_all:
            stmt = stmt.where(UseCaseRun.created_by == actor.id)
        if use_case_id:
            stmt = stmt.where(UseCaseRun.use_case_id == use_case_id)
        if session_id:
            stmt = stmt.where(UseCaseRun.session_id == session_id)
        if status:
            stmt = stmt.where(UseCaseRun.status == status)
        async with self._session(actor, project_id) as s:
            return [self._out(r) for r in (await s.execute(stmt)).scalars()]

    # ------------------------------------------------------------------ sessions
    async def list_sessions(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        use_case_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[SessionOut]:
        """The caller's own sessions, latest first."""
        stmt = (
            select(UseCaseSession, UseCase.name)
            .join(UseCase, UseCase.id == UseCaseSession.use_case_id)
            .where(UseCaseSession.created_by == actor.id)
            .order_by(UseCaseSession.updated_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        if use_case_id:
            stmt = stmt.where(UseCaseSession.use_case_id == use_case_id)
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
            return [await self._session_out(s, sess, name) for sess, name in rows]

    @staticmethod
    async def _session_out(s: AsyncSession, sess: UseCaseSession, name: str) -> SessionOut:
        count, last = (
            await s.execute(
                select(func.count(), func.max(UseCaseRun.created_at)).where(
                    UseCaseRun.session_id == sess.id
                )
            )
        ).one()
        status = (
            await s.execute(
                select(UseCaseRun.status)
                .where(UseCaseRun.session_id == sess.id)
                .order_by(UseCaseRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        del last
        return SessionOut(
            id=sess.id,
            use_case_id=sess.use_case_id,
            use_case_name=name,
            title=sess.title,
            runs=count,
            last_status=status,
            created_by=sess.created_by,
            created_at=sess.created_at,
            updated_at=sess.updated_at,
        )

    async def get_session(
        self, actor: Actor, project_id: uuid.UUID, session_id: uuid.UUID
    ) -> SessionDetail:
        async with self._session(actor, project_id) as s:
            row = (
                await s.execute(
                    select(UseCaseSession, UseCase.name)
                    .join(UseCase, UseCase.id == UseCaseSession.use_case_id)
                    .where(UseCaseSession.id == session_id)
                )
            ).one_or_none()
            if row is None or row[0].created_by != actor.id:
                raise SESSION_NOT_FOUND
            runs = (
                (
                    await s.execute(
                        select(UseCaseRun)
                        .where(UseCaseRun.session_id == session_id)
                        .order_by(UseCaseRun.created_at)
                    )
                )
                .scalars()
                .all()
            )
            base = await self._session_out(s, row[0], row[1])
            return SessionDetail(**base.model_dump(), run_list=[self._out(r) for r in runs])

    # ------------------------------------------------------------------ follow a run
    async def wait(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        see_all: bool,
        inspect: bool,
        timeout_s: float,
        poll_s: float = 0.5,
    ) -> UseCaseRunDetail:
        """The run once it ends, or as it is when `timeout_s` passes (sync delivery)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            detail = await self.get(actor, project_id, run_id, see_all=see_all, inspect=inspect)
            if detail.status in TERMINAL or loop.time() >= deadline:
                return detail
            await asyncio.sleep(poll_s)

    async def watch(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        see_all: bool,
        inspect: bool,
        poll_s: float = WATCH_POLL_S,
    ) -> AsyncIterator[dict[str, Any]]:
        """Progress of a run as events: `run` when the run changes, `step` for each step that
        starts or ends, `done` with the final detail. A connection (or a refreshed page) always
        starts from the full state, so it needs no event ids to pick a running run up."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WATCH_MAX_S
        seen: dict[str, tuple[str, bool]] = {}
        last: tuple[Any, ...] | None = None
        while True:
            detail = await self.get(actor, project_id, run_id, see_all=see_all, inspect=inspect)
            state = (detail.status, detail.steps, detail.cost, detail.tokens_in, detail.tokens_out)
            if state != last:
                last = state
                yield {"type": "run", "run": detail.model_dump(mode="json", exclude={"step_list"})}
            for st in detail.step_list:
                mark = (st.status, st.finished_at is not None)
                if seen.get(st.step_key) != mark:
                    seen[st.step_key] = mark
                    yield {"type": "step", "step": st.model_dump(mode="json")}
            if detail.status in TERMINAL:
                yield {"type": "done", "run": detail.model_dump(mode="json")}
                return
            if loop.time() >= deadline:
                return
            await asyncio.sleep(poll_s)

    # ------------------------------------------------------------------ cancel
    async def cancel(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        see_all: bool,
        inspect: bool = True,
    ) -> UseCaseRunDetail:
        async with self._session(actor, project_id) as s:
            run = await self._visible(s, actor, run_id, see_all=see_all)
            workflow_id = run.workflow_id
            if run.status in TERMINAL:
                return await self.get(actor, project_id, run_id, see_all=see_all, inspect=inspect)
            run.cancel_requested = True
            if run.status == "queued":
                run.status, run.finished_at = "cancelled", utcnow()
                run.webhook_status = None  # the workflow skips a run that is not queued
            await audit.record(
                s, actor, "usecase.run_cancel", "use_case_run", run.id, {}, project_id=project_id
            )
        if workflow_id:
            await self._starter.cancel(workflow_id)
        return await self.get(actor, project_id, run_id, see_all=see_all, inspect=inspect)
