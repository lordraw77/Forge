"""Evaluations of prompts and RAG pipelines (E3.2): datasets of cases, runs that execute a target
over a dataset and score each answer, comparison of two runs.

A run executes inside the API process as a background task (bounded concurrency); progress is
in the database, so any pod can show it. A run left `running` by a restart is marked
`interrupted` when the service starts. Every target call is the normal Prompt Studio test or RAG
query, so each case also leaves its usual trace (`source_run`) and gateway call.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.core.ids import uuid7
from forge.core.models import utcnow
from forge.evals import evaluators as ev
from forge.evals.models import EvalCase, EvalDataset, EvalResult, EvalRun
from forge.evals.schemas import (
    MAX_CASES,
    EvalCaseDiff,
    EvalCaseIn,
    EvalCaseOut,
    EvalComparison,
    EvalDatasetDetail,
    EvalDatasetIn,
    EvalDatasetOut,
    EvalDatasetUpdateIn,
    EvalImportIn,
    EvalResultOut,
    EvalRunDetail,
    EvalRunIn,
    EvalRunOut,
)
from forge.gateway.service import Caller, Gateway
from forge.prompts.service import PromptStudio
from forge.prompts.service import RunIn as PromptRunIn
from forge.rag.service import QueryIn as RagQueryIn
from forge.rag.service import RagStudio

NOT_FOUND = ProblemError(404, "not_found", "Dataset not found")
RUN_NOT_FOUND = ProblemError(404, "not_found", "Evaluation run not found")


def parse_cases(kind: str, fmt: str, text: str) -> list[EvalCaseIn]:
    """JSON Lines (one case object per line) or CSV (header row; prompt datasets: every column
    except `expected` is a variable; RAG: `query`, `expected`, `expected_sources` joined by `;`)."""
    cases: list[EvalCaseIn] = []
    try:
        if fmt == "jsonl":
            for n, line in enumerate(text.splitlines(), start=1):
                if line.strip():
                    try:
                        cases.append(EvalCaseIn.model_validate(json.loads(line)))
                    except (ValueError, ValidationError) as exc:
                        raise ProblemError(422, "invalid_case", f"Line {n}: {exc}"[:300]) from exc
        else:
            for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
                row = {k.strip(): (v or "") for k, v in row.items() if k}
                expected = row.pop("expected", "") or None
                sources = [
                    x.strip() for x in row.pop("expected_sources", "").split(";") if x.strip()
                ]
                if kind == "rag":
                    row = {"query": row.get("query", "")}
                    if not row["query"]:
                        raise ProblemError(422, "invalid_case", f"Row {n}: query is empty")
                cases.append(EvalCaseIn(input=row, expected=expected, expected_sources=sources))
    except csv.Error as exc:
        raise ProblemError(422, "invalid_csv", str(exc)) from exc
    return cases


def _check_cases(kind: str, cases: list[EvalCaseIn]) -> None:
    if len(cases) > MAX_CASES:
        raise ProblemError(422, "too_many_cases", f"At most {MAX_CASES} cases per dataset")
    for i, c in enumerate(cases, start=1):
        if kind == "rag" and not str(c.input.get("query", "")).strip():
            raise ProblemError(422, "invalid_case", f"Case {i}: a RAG case needs input.query")
        if kind == "prompt" and any(not isinstance(v, str | int | float) for v in c.input.values()):
            raise ProblemError(422, "invalid_case", f"Case {i}: variables must be text")


def _problem_text(exc: ProblemError) -> str:
    detail = exc.detail
    if isinstance(detail, list):
        return f"{exc.title}: {', '.join(str(d) for d in detail)}"[:1000]
    return (detail if isinstance(detail, str) else exc.title)[:1000]


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summarise(rows: list[EvalResult], specs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for r in rows if r.passed)
    errors = sum(1 for r in rows if r.error)
    per: dict[str, Any] = {}
    for spec in specs:
        name = ev.label(spec)
        scored = [
            r.scores[name]["score"] for r in rows if r.scores.get(name, {}).get("score") is not None
        ]
        ok = sum(
            1
            for r in rows
            if r.scores.get(name, {}).get("passed") and r.scores[name].get("score") is not None
        )
        per[name] = {
            "mean": round(sum(scored) / len(scored), 4) if scored else None,
            "scored": len(scored),
            "passed": ok,
            "skipped": total - len(scored) - sum(1 for r in rows if r.error),
        }
    lat = [r.latency_ms for r in rows if not r.error]
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed - errors,
        "errors": errors,
        "pass_rate": round(passed / total, 4) if total else None,
        "evaluators": per,
        "tokens": sum(r.tokens for r in rows),
        "cost": str(sum((r.cost for r in rows), Decimal(0))),
        "latency_p50_ms": _percentile(lat, 0.5),
        "latency_p95_ms": _percentile(lat, 0.95),
    }


class Evaluations:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: Gateway,
        prompts: PromptStudio,
        rag: RagStudio,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._prompts = prompts
        self._rag = rag
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._cancelled: set[uuid.UUID] = set()

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    async def sweep(self) -> None:
        """A run whose process died stops beating: after five minutes it is `interrupted`.
        (Another pod may be running it, so a restart alone proves nothing.)"""
        async with admin_scope(self._sessions) as s:
            await s.execute(
                update(EvalRun)
                .where(
                    EvalRun.status.in_(("queued", "running")),
                    EvalRun.heartbeat_at < func.now() - text("interval '5 minutes'"),
                )
                .values(status="interrupted", finished_at=utcnow())
            )

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ------------------------------------------------------------ datasets
    @staticmethod
    def _out(d: EvalDataset, count: int) -> EvalDatasetOut:
        return EvalDatasetOut(
            id=d.id,
            name=d.name,
            kind=d.kind,
            description=d.description,
            cases=count,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )

    @staticmethod
    async def _dataset(s: AsyncSession, dataset_id: uuid.UUID) -> EvalDataset:
        d = await s.get(EvalDataset, dataset_id)
        if d is None:
            raise NOT_FOUND
        return d

    @staticmethod
    async def _replace_cases(
        s: AsyncSession, d: EvalDataset, cases: list[EvalCaseIn], *, append: bool = False
    ) -> None:
        start = 0
        if append:
            start = (
                await s.execute(
                    select(func.coalesce(func.max(EvalCase.position), 0)).where(
                        EvalCase.dataset_id == d.id
                    )
                )
            ).scalar_one()
        else:
            await s.execute(delete(EvalCase).where(EvalCase.dataset_id == d.id))
        for i, c in enumerate(cases, start=start + 1):
            s.add(
                EvalCase(
                    project_id=d.project_id,
                    dataset_id=d.id,
                    position=i,
                    input=c.input,
                    expected=c.expected,
                    expected_sources=c.expected_sources,
                )
            )

    async def _detail(self, s: AsyncSession, d: EvalDataset) -> EvalDatasetDetail:
        rows = (
            (
                await s.execute(
                    select(EvalCase).where(EvalCase.dataset_id == d.id).order_by(EvalCase.position)
                )
            )
            .scalars()
            .all()
        )
        base = self._out(d, len(rows))
        return EvalDatasetDetail(
            **base.model_dump(), items=[EvalCaseOut.model_validate(r) for r in rows]
        )

    async def list_datasets(
        self, actor: Actor, project_id: uuid.UUID, kind: str | None = None
    ) -> list[EvalDatasetOut]:
        async with self._session(actor, project_id) as s:
            counts = (
                select(EvalCase.dataset_id, func.count().label("n"))
                .group_by(EvalCase.dataset_id)
                .subquery()
            )
            q = (
                select(EvalDataset, func.coalesce(counts.c.n, 0))
                .outerjoin(counts, counts.c.dataset_id == EvalDataset.id)
                .order_by(EvalDataset.updated_at.desc())
            )
            if kind:
                q = q.where(EvalDataset.kind == kind)
            return [self._out(d, n) for d, n in (await s.execute(q)).all()]

    async def get_dataset(
        self, actor: Actor, project_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> EvalDatasetDetail:
        async with self._session(actor, project_id) as s:
            return await self._detail(s, await self._dataset(s, dataset_id))

    async def create_dataset(
        self, actor: Actor, project_id: uuid.UUID, body: EvalDatasetIn
    ) -> EvalDatasetDetail:
        _check_cases(body.kind, body.cases)
        async with self._session(actor, project_id) as s:
            d = EvalDataset(
                id=uuid7(),
                project_id=project_id,
                name=body.name,
                kind=body.kind,
                description=body.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            s.add(d)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A dataset with this name exists") from exc
            await self._replace_cases(s, d, body.cases)
            await audit.record(
                s,
                actor,
                "eval_dataset.create",
                "eval_dataset",
                d.id,
                {"name": body.name, "kind": body.kind, "cases": len(body.cases)},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, d)

    async def update_dataset(
        self, actor: Actor, project_id: uuid.UUID, dataset_id: uuid.UUID, body: EvalDatasetUpdateIn
    ) -> EvalDatasetDetail:
        async with self._session(actor, project_id) as s:
            d = await self._dataset(s, dataset_id)
            if body.name is not None:
                d.name = body.name
            if body.description is not None:
                d.description = body.description
            if body.cases is not None:
                _check_cases(d.kind, body.cases)
                await self._replace_cases(s, d, body.cases)
            d.updated_by = actor.id
            d.updated_at = utcnow()
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A dataset with this name exists") from exc
            await audit.record(
                s, actor, "eval_dataset.update", "eval_dataset", d.id, None, project_id=project_id
            )
            await s.flush()
            return await self._detail(s, d)

    async def import_cases(
        self, actor: Actor, project_id: uuid.UUID, dataset_id: uuid.UUID, body: EvalImportIn
    ) -> EvalDatasetDetail:
        async with self._session(actor, project_id) as s:
            d = await self._dataset(s, dataset_id)
            cases = parse_cases(d.kind, body.format, body.text)
            _check_cases(d.kind, cases)
            have = (
                await s.execute(select(func.count()).where(EvalCase.dataset_id == d.id))
            ).scalar_one()
            if not body.replace and have + len(cases) > MAX_CASES:
                raise ProblemError(422, "too_many_cases", f"At most {MAX_CASES} cases per dataset")
            await self._replace_cases(s, d, cases, append=not body.replace)
            d.updated_at = utcnow()
            await s.flush()
            return await self._detail(s, d)

    async def delete_dataset(
        self, actor: Actor, project_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> None:
        async with self._session(actor, project_id) as s:
            d = await self._dataset(s, dataset_id)
            await audit.record(
                s,
                actor,
                "eval_dataset.delete",
                "eval_dataset",
                d.id,
                {"name": d.name},
                project_id=project_id,
            )
            await s.delete(d)

    # ------------------------------------------------------------ runs
    async def create_run(self, actor: Actor, project_id: uuid.UUID, body: EvalRunIn) -> EvalRunOut:
        async with self._session(actor, project_id) as s:
            d = await self._dataset(s, body.dataset_id)
            kind, dname = d.kind, d.name
            total = (
                await s.execute(select(func.count()).where(EvalCase.dataset_id == d.id))
            ).scalar_one()
        if total == 0:
            raise ProblemError(422, "empty_dataset", "The dataset has no cases")
        try:
            for spec in body.evaluators:
                ev.validate(kind, spec)
        except ev.EvaluatorError as exc:
            raise ProblemError(422, "invalid_evaluator", str(exc)) from exc
        names = [ev.label(x) for x in body.evaluators]
        if len(set(names)) != len(names):
            raise ProblemError(
                422, "invalid_evaluator", "Give evaluators of one type a distinct name"
            )
        if kind == "prompt":
            p = await self._prompts.get(actor, project_id, body.target_id)
            tname, version = p.name, body.target_version or p.latest_version
        else:
            rp = await self._rag.get(actor, project_id, body.target_id)
            tname, version = rp.name, body.target_version or rp.latest_version
        run = EvalRun(
            id=uuid7(),
            project_id=project_id,
            dataset_id=body.dataset_id,
            dataset_name=dname,
            kind=kind,
            name=body.name,
            target_id=body.target_id,
            target_name=tname,
            target_version=version,
            model=body.model if kind == "prompt" else None,
            evaluators=body.evaluators,
            total=total,
            created_by=actor.id,
        )
        async with self._session(actor, project_id) as s:
            s.add(run)
            await audit.record(
                s,
                actor,
                "eval_run.start",
                "eval_run",
                run.id,
                {"dataset": dname, "target": tname, "version": version},
                project_id=project_id,
            )
            await s.flush()
            out = EvalRunOut.model_validate(run)
        task = asyncio.create_task(self._execute(actor, project_id, run.id, body.concurrency))
        self._tasks[run.id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run.id, None))
        return out

    async def _set(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, **values: Any
    ) -> None:
        async with self._session(actor, project_id) as s:
            await s.execute(update(EvalRun).where(EvalRun.id == run_id).values(**values))

    async def _beat(self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(30)
            await self._set(actor, project_id, run_id, heartbeat_at=utcnow())

    async def _execute(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, concurrency: int
    ) -> None:
        try:
            async with self._session(actor, project_id) as s:
                run = await s.get(EvalRun, run_id)
                assert run is not None
                run.status = "running"
                cases = (
                    (
                        await s.execute(
                            select(EvalCase)
                            .where(EvalCase.dataset_id == run.dataset_id)
                            .order_by(EvalCase.position)
                        )
                    )
                    .scalars()
                    .all()
                )
                s.expunge(run)
                for c in cases:
                    s.expunge(c)
            sem = asyncio.Semaphore(concurrency)
            beat = asyncio.create_task(self._beat(actor, project_id, run_id))

            async def one(case: EvalCase) -> None:
                async with sem:
                    if run_id in self._cancelled:
                        return
                    row = await self._case(actor, project_id, run, case)
                    async with self._session(actor, project_id) as s:
                        s.add(row)
                        await s.execute(
                            update(EvalRun)
                            .where(EvalRun.id == run_id)
                            .values(completed=EvalRun.completed + 1)
                        )

            try:
                await asyncio.gather(*(one(c) for c in cases))
            finally:
                beat.cancel()
            async with self._session(actor, project_id) as s:
                rows = (
                    (await s.execute(select(EvalResult).where(EvalResult.run_id == run_id)))
                    .scalars()
                    .all()
                )
                summary = summarise(list(rows), run.evaluators)
            cancelled = run_id in self._cancelled
            await self._set(
                actor,
                project_id,
                run_id,
                status="cancelled" if cancelled else "done",
                summary=summary,
                finished_at=utcnow(),
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._set(actor, project_id, run_id, status="interrupted", finished_at=utcnow())
            )
            raise
        except Exception:
            await self._set(actor, project_id, run_id, status="interrupted", finished_at=utcnow())
            raise
        finally:
            self._cancelled.discard(run_id)

    async def _case(
        self, actor: Actor, project_id: uuid.UUID, run: EvalRun, case: EvalCase
    ) -> EvalResult:
        row = EvalResult(
            id=uuid7(),
            project_id=project_id,
            run_id=run.id,
            case_id=case.id,
            position=case.position,
            input=case.input,
            expected=case.expected,
        )
        started = time.perf_counter()
        context: str | None = None
        retrieved: list[str] = []
        try:
            if run.kind == "prompt":
                pr = await self._prompts.test(
                    actor,
                    project_id,
                    run.target_id,
                    PromptRunIn(
                        version=run.target_version,
                        variables={k: str(v) for k, v in case.input.items()},
                        model=run.model,
                    ),
                )
                if pr.status != "ok":
                    raise ProblemError(502, pr.error_code or "run_failed", pr.error or "Run failed")
                row.output, row.source_run = pr.output or "", pr.id
                row.tokens, row.cost, row.latency_ms = pr.usage.total_tokens, pr.cost, pr.latency_ms
                inputs = json.dumps(case.input, ensure_ascii=False)
            else:
                query = str(case.input["query"])
                rr = await self._rag.query(
                    actor,
                    project_id,
                    run.target_id,
                    RagQueryIn(query=query, version=run.target_version),
                )
                if rr.status == "error":
                    raise ProblemError(502, rr.error_code or "run_failed", rr.error or "Run failed")
                row.output, row.source_run = rr.answer or "", rr.id
                row.tokens, row.cost, row.latency_ms = rr.total_tokens, rr.cost, rr.latency_ms
                retrieved = [x["document_name"] for x in rr.retrieval.get("retrieved", [])]
                context = "\n\n".join(f"[{c.index}] {c.excerpt}" for c in rr.citations)
                inputs = query
            scores = await self._score(
                actor, project_id, run, case, row, inputs, context, retrieved
            )
            row.scores = {k: v.out() for k, v in scores.items()}
            applied = [v for v in scores.values() if v.score is not None]
            row.passed = bool(applied) and all(v.passed for v in applied)
        except ProblemError as exc:
            row.error = _problem_text(exc)
            row.latency_ms = int((time.perf_counter() - started) * 1000)
        except ev.EvaluatorError as exc:
            row.error = str(exc)[:1000]
        return row

    async def _score(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run: EvalRun,
        case: EvalCase,
        row: EvalResult,
        inputs: str,
        context: str | None,
        retrieved: list[str],
    ) -> dict[str, ev.Score]:
        out: dict[str, ev.Score] = {}
        for spec in run.evaluators:
            t = spec["type"]
            if t in ev.JUDGE_TYPES:
                if not ev.judge_needs(spec, case.expected):
                    s: ev.Score | None = None
                else:
                    s = await self._judge(actor, project_id, spec, row, inputs, context)
            elif t in ("recall_at_k", "mrr"):
                s = ev.retrieval(spec, retrieved, case.expected_sources)
            else:
                s = ev.deterministic(spec, row.output or "", case.expected)
            out[ev.label(spec)] = s or ev.Score(None, True, "skipped: nothing to compare with")
        return out

    async def _judge(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        spec: dict[str, Any],
        row: EvalResult,
        inputs: str,
        context: str | None,
    ) -> ev.Score:
        caller = Caller(project_id=project_id, user_id=actor.id, trace_id=f"eval_run:{row.run_id}")
        messages = ev.judge_messages(
            spec, inputs=inputs, output=row.output or "", expected=row.expected, context=context
        )
        try:
            response = await self._gateway.service.chat(
                caller, str(spec["model"]), {"messages": messages, "temperature": 0}
            )
        except ProblemError as exc:
            return ev.Score(0.0, False, f"judge call failed: {exc.code}")
        body = response.body or {}
        text = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        if response.info:
            row.tokens += response.info.tokens_in + response.info.tokens_out
            row.cost += response.info.cost
        return ev.parse_judge(text, spec)

    # ------------------------------------------------------------ reading
    async def list_runs(
        self, actor: Actor, project_id: uuid.UUID, dataset_id: uuid.UUID | None, limit: int
    ) -> list[EvalRunOut]:
        await self.sweep()
        async with self._session(actor, project_id) as s:
            q = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
            if dataset_id:
                q = q.where(EvalRun.dataset_id == dataset_id)
            return [EvalRunOut.model_validate(r) for r in (await s.execute(q)).scalars()]

    async def get_run(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, *, failed_only: bool = False
    ) -> EvalRunDetail:
        await self.sweep()
        async with self._session(actor, project_id) as s:
            run = await s.get(EvalRun, run_id)
            if run is None:
                raise RUN_NOT_FOUND
            q = select(EvalResult).where(EvalResult.run_id == run_id).order_by(EvalResult.position)
            if failed_only:
                q = q.where(EvalResult.passed.is_(False))
            rows = (await s.execute(q)).scalars().all()
            return EvalRunDetail(
                **EvalRunOut.model_validate(run).model_dump(),
                results=[EvalResultOut.model_validate(r) for r in rows],
            )

    async def cancel_run(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> EvalRunOut:
        async with self._session(actor, project_id) as s:
            run = await s.get(EvalRun, run_id)
            if run is None:
                raise RUN_NOT_FOUND
            if run.status not in ("queued", "running"):
                raise ProblemError(409, "not_running", "The run is not running")
        self._cancelled.add(run_id)
        return (await self.get_run(actor, project_id, run_id)).model_copy(update={"results": []})

    async def delete_run(self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        async with self._session(actor, project_id) as s:
            run = await s.get(EvalRun, run_id)
            if run is None:
                raise RUN_NOT_FOUND
            if run.status in ("queued", "running"):
                raise ProblemError(409, "running", "Cancel the run first")
            await s.delete(run)

    async def compare(
        self, actor: Actor, project_id: uuid.UUID, a_id: uuid.UUID, b_id: uuid.UUID
    ) -> EvalComparison:
        a = await self.get_run(actor, project_id, a_id)
        b = await self.get_run(actor, project_id, b_id)
        if a.kind != b.kind:
            raise ProblemError(422, "kind_mismatch", "Only runs of the same kind can be compared")
        ra = {r.case_id or r.position: r for r in a.results}
        rb = {r.case_id or r.position: r for r in b.results}
        cases: list[EvalCaseDiff] = []
        for key in sorted(ra.keys() | rb.keys(), key=lambda k: (ra.get(k) or rb[k]).position):
            x, y = ra.get(key), rb.get(key)
            if x is None or y is None:
                change = "only_b" if x is None else "only_a"
            elif x.passed == y.passed:
                change = "same"
            else:
                change = "improved" if y.passed else "regressed"
            ref = x or y
            assert ref is not None
            cases.append(
                EvalCaseDiff(
                    position=ref.position,
                    input=ref.input,
                    a_passed=x.passed if x else None,
                    b_passed=y.passed if y else None,
                    change=change,
                    a_output=x.output if x else None,
                    b_output=y.output if y else None,
                )
            )
        deltas: dict[str, float] = {}
        for name, sa in (a.summary.get("evaluators") or {}).items():
            sb = (b.summary.get("evaluators") or {}).get(name)
            if sb and sa.get("mean") is not None and sb.get("mean") is not None:
                deltas[name] = round(sb["mean"] - sa["mean"], 4)
        pa, pb = a.summary.get("pass_rate"), b.summary.get("pass_rate")
        return EvalComparison(
            a=EvalRunOut.model_validate(a.model_dump(exclude={"results"})),
            b=EvalRunOut.model_validate(b.model_dump(exclude={"results"})),
            pass_rate_delta=round(pb - pa, 4) if pa is not None and pb is not None else None,
            evaluator_deltas=deltas,
            regressions=sum(1 for c in cases if c.change == "regressed"),
            improvements=sum(1 for c in cases if c.change == "improved"),
            cases=cases,
        )
