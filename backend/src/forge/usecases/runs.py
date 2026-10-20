"""Starting, reading and cancelling use case runs (API side of M2.3b)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jsonschema
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.agents.service import RunStarter
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.observability.service import current_trace_id
from forge.usecases import definition as defs
from forge.usecases import graph as g
from forge.usecases.models import UseCase, UseCaseRun, UseCaseRunStep, UseCaseVersion
from forge.usecases.schemas import (
    RunStartIn,
    RunStepOut,
    UseCaseReferenceOut,
    UseCaseRunDetail,
    UseCaseRunOut,
)
from forge.usecases.service import NOT_FOUND, UseCaseStudio

RUN_WORKFLOW = "UseCaseRunWorkflow"
TERMINAL = ("succeeded", "failed", "cancelled")
RUN_NOT_FOUND = ProblemError(404, "not_found", "Run not found")


class UseCaseRuns:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        studio: UseCaseStudio,
        starter: RunStarter,
    ) -> None:
        self._sessions = sessions
        self._studio = studio
        self._starter = starter
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

    async def start(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        use_case_id: uuid.UUID,
        body: RunStartIn,
        *,
        can_build: bool,
    ) -> UseCaseRunDetail:
        if body.mode == "test" and not can_build:
            raise ProblemError(403, "forbidden", "Test runs need permission 'usecase.build'")
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
        graph = dict(g.executable_graph(snap.pattern, snap.definition, names))
        graph["_output"] = {"type": snap.output_type, "schema": snap.output_schema}
        limits = g.limits_of(graph)
        defaults = snap.defaults or {}
        limits["max_cost_usd"] = min(
            limits["max_cost_usd"], float(defaults.get("max_cost_usd", 5.0))
        )
        limits["timeout_s"] = min(limits["timeout_s"], int(defaults.get("max_duration_s", 1800)))
        async with self._session(actor, project_id) as s:
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
        return await self.get(actor, project_id, run_id, see_all=True)

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
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, *, see_all: bool
    ) -> UseCaseRunDetail:
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
            graph = {k: v for k, v in run.graph.items() if not k.startswith("_")}
            return UseCaseRunDetail(
                **self._out(run).model_dump(),
                inputs=run.inputs,
                outputs=run.outputs,
                limits=run.limits,
                graph=graph,
                step_list=[
                    RunStepOut.model_validate({**st.__dict__, "cost": float(st.cost)})
                    for st in steps
                ],
            )

    async def list_runs(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        see_all: bool,
        use_case_id: uuid.UUID | None = None,
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
        if status:
            stmt = stmt.where(UseCaseRun.status == status)
        async with self._session(actor, project_id) as s:
            return [self._out(r) for r in (await s.execute(stmt)).scalars()]

    # ------------------------------------------------------------------ cancel
    async def cancel(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, *, see_all: bool
    ) -> UseCaseRunDetail:
        async with self._session(actor, project_id) as s:
            run = await self._visible(s, actor, run_id, see_all=see_all)
            workflow_id = run.workflow_id
            if run.status in TERMINAL:
                return await self.get(actor, project_id, run_id, see_all=see_all)
            run.cancel_requested = True
            if run.status == "queued":
                run.status, run.finished_at = "cancelled", utcnow()
            await audit.record(
                s, actor, "usecase.run_cancel", "use_case_run", run.id, {}, project_id=project_id
            )
        if workflow_id:
            await self._starter.cancel(workflow_id)
        return await self.get(actor, project_id, run_id, see_all=see_all)
