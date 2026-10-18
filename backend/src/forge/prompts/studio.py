"""Prompt Studio: library, immutable versions, tags, test, playground, Help Me Write
(E1.10 to E1.13). Every query runs in the caller's project scope (RLS, ADR-002); model calls go
through the gateway pipeline (ADR-005) and each execution is stored as a `prompt_run` trace.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.ids import uuid7
from forge.core.models import utcnow
from forge.gateway.service import Caller, Gateway
from forge.prompts import assist, templates
from forge.prompts.models import Prompt, PromptRun, PromptTag, PromptVersion
from forge.prompts.schemas import (
    AssistIn,
    AssistOut,
    ParseIn,
    ParseOut,
    PlaygroundIn,
    PromptCreateIn,
    PromptDetail,
    PromptOut,
    PromptPage,
    PromptUpdateIn,
    ReferenceOut,
    RunDetail,
    RunIn,
    RunOut,
    TemplateIssue,
    Usage,
    Variable,
    VersionIn,
    VersionOut,
    VersionSummary,
)

CHAT_KINDS = frozenset({"chat"})
TAG_ORDER = ("draft", "staging", "prod")
NOT_FOUND = ProblemError(404, "not_found", "Prompt not found")


def _encode_cursor(updated_at: datetime, prompt_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{prompt_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, pid = raw.split("|", 1)
        return datetime.fromisoformat(stamp), uuid.UUID(pid)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid pagination cursor") from exc


def _issues(system: str, user: str) -> tuple[list[str], list[TemplateIssue]]:
    names: list[str] = []
    issues: list[TemplateIssue] = []
    for field, source in (("system_template", system), ("user_template", user)):
        try:
            templates.check(source)
            names += [n for n in templates.variables(source) if n not in names]
        except templates.TemplateError as exc:
            issues.append(TemplateIssue(field=field, line=exc.line, message=exc.message))
    return names, issues


class PromptStudio:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], gateway: Gateway) -> None:
        self._sessions = sessions
        self._gateway = gateway

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    # ------------------------------------------------------------ helpers
    @staticmethod
    async def _prompt(s: AsyncSession, prompt_id: uuid.UUID, *, lock: bool = False) -> Prompt:
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        if lock:
            stmt = stmt.with_for_update()
        prompt = (await s.execute(stmt)).scalar_one_or_none()
        if prompt is None:
            raise NOT_FOUND
        return prompt

    @staticmethod
    async def _version(s: AsyncSession, prompt: Prompt, number: int | None) -> PromptVersion:
        n = number or prompt.latest_version
        row = (
            await s.execute(
                select(PromptVersion).where(
                    PromptVersion.prompt_id == prompt.id, PromptVersion.version == n
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ProblemError(404, "version_not_found", f"Version {n} not found")
        return row

    @staticmethod
    async def _tags(
        s: AsyncSession, prompt_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, int]]:
        out: dict[uuid.UUID, dict[str, int]] = {pid: {} for pid in prompt_ids}
        if prompt_ids:
            rows = await s.execute(
                select(PromptTag.prompt_id, PromptTag.tag, PromptTag.version).where(
                    PromptTag.prompt_id.in_(prompt_ids)
                )
            )
            for pid, tag, version in rows.all():
                out[pid][tag] = version
        return out

    @staticmethod
    def _out(prompt: Prompt, tags: dict[str, int], model: str) -> PromptOut:
        return PromptOut(
            id=prompt.id,
            project_id=prompt.project_id,
            name=prompt.name,
            description=prompt.description,
            status="published" if "prod" in tags else "draft",
            latest_version=prompt.latest_version,
            published_version=tags.get("prod"),
            tags=tags,
            model=model,
            created_at=prompt.created_at,
            created_by=prompt.created_by,
            updated_at=prompt.updated_at,
        )

    @staticmethod
    def _version_out(row: PromptVersion, tags: dict[str, int]) -> VersionOut:
        return VersionOut.model_validate(
            {
                "version": row.version,
                "model": row.model,
                "system_template": row.system_template,
                "user_template": row.user_template,
                "variables": row.variables,
                "examples": row.examples,
                "model_params": row.model_params,
                "note": row.note,
                "tags": [t for t in TAG_ORDER if tags.get(t) == row.version],
                "created_at": row.created_at,
                "created_by": row.created_by,
            }
        )

    async def _detail(
        self, s: AsyncSession, prompt: Prompt, number: int | None = None
    ) -> PromptDetail:
        version = await self._version(s, prompt, number)
        tags = (await self._tags(s, [prompt.id]))[prompt.id]
        out = self._out(prompt, tags, version.model)
        return PromptDetail(**out.model_dump(), version=self._version_out(version, tags))

    def _check_model(self, project_id: uuid.UUID, model: str) -> None:
        self._gateway.registry.resolve(project_id, model, CHAT_KINDS)

    def _prepare(self, project_id: uuid.UUID, body: VersionIn) -> list[dict[str, Any]]:
        """Validate templates and model; add detected variables the editor did not declare."""
        names, issues = _issues(body.system_template, body.user_template)
        if issues:
            raise ProblemError(
                422,
                "template_invalid",
                "The prompt template is invalid",
                [i.model_dump() for i in issues],
            )
        self._check_model(project_id, body.model)
        declared = {v.name for v in body.variables}
        variables = list(body.variables) + [Variable(name=n) for n in names if n not in declared]
        return [v.model_dump() for v in variables]

    async def _add_version(
        self, s: AsyncSession, actor: Actor, prompt: Prompt, body: VersionIn
    ) -> PromptVersion:
        variables = self._prepare(prompt.project_id, body)
        prompt.latest_version += 1
        prompt.updated_by = actor.id
        row = PromptVersion(
            project_id=prompt.project_id,
            prompt_id=prompt.id,
            version=prompt.latest_version,
            model=body.model,
            system_template=body.system_template,
            user_template=body.user_template,
            variables=variables,
            examples=[e.model_dump() for e in body.examples],
            model_params=body.model_params.model_dump(exclude_none=True),
            note=body.note,
            created_by=actor.id,
        )
        s.add(row)
        await s.flush()
        await self._move_tag(s, actor, prompt, "draft", row.version)
        return row

    @staticmethod
    async def _move_tag(
        s: AsyncSession, actor: Actor, prompt: Prompt, tag: str, version: int
    ) -> None:
        row = await s.get(PromptTag, (prompt.id, tag))
        if row is None:
            s.add(
                PromptTag(
                    prompt_id=prompt.id,
                    project_id=prompt.project_id,
                    tag=tag,
                    version=version,
                    updated_by=actor.id,
                )
            )
        else:
            row.version = version
            row.updated_by = actor.id
        await s.flush()

    @staticmethod
    async def _insert_prompt(s: AsyncSession, prompt: Prompt) -> None:
        """Insert; a concurrent create with the same name hits the unique index here."""
        s.add(prompt)
        try:
            await s.flush()
        except IntegrityError as exc:
            raise ProblemError(409, "name_taken", "A prompt with this name exists") from exc

    @staticmethod
    async def _name_free(s: AsyncSession, name: str, exclude: uuid.UUID | None = None) -> None:
        stmt = select(Prompt.id).where(func.lower(Prompt.name) == name.lower())
        if exclude:
            stmt = stmt.where(Prompt.id != exclude)
        if (await s.execute(stmt)).first():
            raise ProblemError(409, "name_taken", f"A prompt named '{name}' already exists")

    # ------------------------------------------------------------ library
    async def list_prompts(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        status: str | None = None,
        q: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> PromptPage:
        limit = max(1, min(limit, 200))
        published = exists().where(PromptTag.prompt_id == Prompt.id, PromptTag.tag == "prod")
        stmt = (
            select(Prompt, PromptVersion.model)
            .join(
                PromptVersion,
                and_(
                    PromptVersion.prompt_id == Prompt.id,
                    PromptVersion.version == Prompt.latest_version,
                ),
            )
            .order_by(Prompt.updated_at.desc(), Prompt.id.desc())
            .limit(limit + 1)
        )
        if status == "published":
            stmt = stmt.where(published)
        elif status == "draft":
            stmt = stmt.where(~published)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(Prompt.name.ilike(pattern, escape="\\"))
        if since:
            stmt = stmt.where(Prompt.updated_at >= since)
        if until:
            stmt = stmt.where(Prompt.updated_at < until)
        if cursor:
            at, pid = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(Prompt.updated_at < at, and_(Prompt.updated_at == at, Prompt.id < pid))
            )
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
            page = rows[:limit]
            tags = await self._tags(s, [p.id for p, _ in page])
        items = [self._out(p, tags[p.id], model) for p, model in page]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1][0]
            next_cursor = _encode_cursor(last.updated_at, last.id)
        return PromptPage(items=items, next_cursor=next_cursor)

    async def get(self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID) -> PromptDetail:
        async with self._session(actor, project_id) as s:
            return await self._detail(s, await self._prompt(s, prompt_id))

    async def create(
        self, actor: Actor, project_id: uuid.UUID, body: PromptCreateIn
    ) -> PromptDetail:
        async with self._session(actor, project_id) as s:
            await self._name_free(s, body.name)
            prompt = Prompt(
                project_id=project_id,
                name=body.name,
                description=body.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            await self._insert_prompt(s, prompt)
            await self._add_version(s, actor, prompt, body.version)
            await audit.record(
                s,
                actor,
                "prompt.create",
                "prompt",
                prompt.id,
                {"name": body.name, "model": body.version.model},
                project_id=project_id,
            )
            return await self._detail(s, prompt)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, body: PromptUpdateIn
    ) -> PromptDetail:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id, lock=True)
            diff: dict[str, Any] = {}
            if body.name is not None and body.name != prompt.name:
                await self._name_free(s, body.name, exclude=prompt.id)
                diff["name"] = [prompt.name, body.name]
                prompt.name = body.name
            if body.description is not None and body.description != prompt.description:
                diff["description"] = "changed"
                prompt.description = body.description
            if diff:
                prompt.updated_by = actor.id
                await audit.record(
                    s, actor, "prompt.update", "prompt", prompt.id, diff, project_id=project_id
                )
                await s.flush()
            return await self._detail(s, prompt)

    async def references(self, s: AsyncSession, prompt_id: uuid.UUID) -> list[ReferenceOut]:
        """Resources that use the prompt. Agents (E1.18) and RAG pipelines (E1.17) add
        their lookups here when they arrive; until then nothing can reference a prompt."""
        return []

    async def delete(self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID) -> None:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id, lock=True)
            refs = await self.references(s, prompt.id)
            if refs:
                raise ProblemError(
                    409,
                    "prompt_in_use",
                    "The prompt is used by other resources",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s,
                actor,
                "prompt.delete",
                "prompt",
                prompt.id,
                {"name": prompt.name, "versions": prompt.latest_version},
                project_id=project_id,
            )
            await s.delete(prompt)

    async def save_version(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, body: VersionIn
    ) -> PromptDetail:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id, lock=True)
            row = await self._add_version(s, actor, prompt, body)
            await audit.record(
                s,
                actor,
                "prompt.version",
                "prompt",
                prompt.id,
                {"version": row.version, "model": row.model},
                project_id=project_id,
            )
            return await self._detail(s, prompt)

    async def versions(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID
    ) -> list[VersionSummary]:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id)
            tags = (await self._tags(s, [prompt.id]))[prompt.id]
            rows = await s.execute(
                select(PromptVersion)
                .where(PromptVersion.prompt_id == prompt.id)
                .order_by(PromptVersion.version.desc())
            )
            return [
                VersionSummary(
                    version=r.version,
                    model=r.model,
                    note=r.note,
                    tags=[t for t in TAG_ORDER if tags.get(t) == r.version],
                    created_at=r.created_at,
                    created_by=r.created_by,
                )
                for r in rows.scalars()
            ]

    async def version(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, number: int
    ) -> VersionOut:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id)
            row = await self._version(s, prompt, number)
            tags = (await self._tags(s, [prompt.id]))[prompt.id]
            return self._version_out(row, tags)

    async def set_tag(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        prompt_id: uuid.UUID,
        tag: str,
        number: int | None,
    ) -> PromptDetail:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id, lock=True)
            row = await self._version(s, prompt, number)
            await self._move_tag(s, actor, prompt, tag, row.version)
            prompt.updated_by = actor.id
            prompt.updated_at = utcnow()
            action = "prompt.publish" if tag == "prod" else "prompt.tag"
            await audit.record(
                s,
                actor,
                action,
                "prompt",
                prompt.id,
                {"tag": tag, "version": row.version},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, prompt)

    async def remove_tag(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, tag: str
    ) -> PromptDetail:
        """`staging` and `prod` can be removed (unpublish); `draft` always follows the latest."""
        if tag == "draft":
            raise ProblemError(400, "tag_fixed", "The draft tag follows the latest version")
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id, lock=True)
            row = await s.get(PromptTag, (prompt.id, tag))
            if row is not None:
                await s.delete(row)
                action = "prompt.unpublish" if tag == "prod" else "prompt.untag"
                await audit.record(
                    s, actor, action, "prompt", prompt.id, {"tag": tag}, project_id=project_id
                )
                await s.flush()
            return await self._detail(s, prompt)

    async def duplicate(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, name: str
    ) -> PromptDetail:
        async with self._session(actor, project_id) as s:
            source = await self._prompt(s, prompt_id)
            version = await self._version(s, source, None)
            await self._name_free(s, name)
            prompt = Prompt(
                project_id=project_id,
                name=name,
                description=source.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            await self._insert_prompt(s, prompt)
            body = VersionIn.model_validate(
                {
                    "model": version.model,
                    "system_template": version.system_template,
                    "user_template": version.user_template,
                    "variables": version.variables,
                    "examples": version.examples,
                    "model_params": version.model_params,
                    "note": f"Duplicated from {source.name} v{version.version}",
                }
            )
            await self._add_version(s, actor, prompt, body)
            await audit.record(
                s,
                actor,
                "prompt.duplicate",
                "prompt",
                prompt.id,
                {"source": str(source.id), "source_version": version.version, "name": name},
                project_id=project_id,
            )
            return await self._detail(s, prompt)

    @staticmethod
    def parse(body: ParseIn) -> ParseOut:
        names, issues = _issues(body.system_template, body.user_template)
        return ParseOut(variables=names, issues=issues)

    # ------------------------------------------------------------ execution
    async def _render(
        self, version: PromptVersion, given: dict[str, str]
    ) -> tuple[dict[str, str], list[dict[str, str]]]:
        declared = {v["name"]: v for v in version.variables}
        unknown = sorted(set(given) - set(declared))
        if unknown:
            raise ProblemError(422, "unknown_variables", "Unknown variables", unknown)
        values = {name: v.get("value", "") for name, v in declared.items()} | given
        required = [n for n, v in declared.items() if v.get("required", True)]
        if missing := templates.missing_required(required, values):
            raise ProblemError(422, "missing_variables", "Required variables are missing", missing)
        try:
            system = await asyncio.to_thread(templates.render, version.system_template, values)
            user = await asyncio.to_thread(templates.render, version.user_template, values)
        except templates.TemplateError as exc:
            raise ProblemError(
                422, "template_render_failed", "The prompt could not be rendered", exc.message
            ) from exc
        messages: list[dict[str, str]] = []
        if system.strip():
            messages.append({"role": "system", "content": system})
        for example in version.examples:
            messages.append({"role": "user", "content": example["input"]})
            messages.append({"role": "assistant", "content": example["output"]})
        if user.strip():
            messages.append({"role": "user", "content": user})
        return values, messages

    async def _execute(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        kind: str,
        model: str,
        messages: list[dict[str, str]],
        params: dict[str, Any],
        values: dict[str, str],
        prompt: Prompt | None = None,
        version: int | None = None,
        group_id: uuid.UUID | None = None,
    ) -> PromptRun:
        run_id = uuid7()
        caller = Caller(project_id=project_id, user_id=actor.id, trace_id=f"prompt_run:{run_id}")
        run = PromptRun(
            id=run_id,
            project_id=project_id,
            prompt_id=prompt.id if prompt else None,
            version=version,
            kind=kind,
            group_id=group_id,
            model=model,
            variables=values,
            messages=messages,
            params=params,
            created_by=actor.id,
        )
        started = time.perf_counter()
        try:
            response = await self._gateway.service.chat(
                caller, model, dict(params) | {"messages": messages}
            )
        except ProblemError as exc:
            run.status = "error"
            run.error_code = exc.code
            detail = exc.detail if isinstance(exc.detail, str) else None
            run.error = (detail or exc.title)[:2000]
            run.latency_ms = int((time.perf_counter() - started) * 1000)
        else:
            body = response.body or {}
            choice = (body.get("choices") or [{}])[0]
            run.status = "ok"
            run.output = str((choice.get("message") or {}).get("content") or "")
            run.finish_reason = choice.get("finish_reason")
            info = response.info
            usage = body.get("usage") or {}
            run.model_name = info.model_name if info else body.get("model")
            run.tokens_in = info.tokens_in if info else int(usage.get("prompt_tokens") or 0)
            run.tokens_out = info.tokens_out if info else int(usage.get("completion_tokens") or 0)
            run.cost = info.cost if info else Decimal(0)
            run.cache_hit = info.cache_hit if info else False
            run.llm_call_id = info.call_id if info else None
            run.latency_ms = int((time.perf_counter() - started) * 1000)
        async with self._session(actor, project_id) as s:
            s.add(run)
        return run

    @staticmethod
    def _run_out(run: PromptRun, detail: bool = True) -> RunOut:
        values: dict[str, Any] = {
            "id": run.id,
            "prompt_id": run.prompt_id,
            "version": run.version,
            "kind": run.kind,
            "group_id": run.group_id,
            "model": run.model,
            "model_name": run.model_name,
            "status": run.status,
            "error_code": run.error_code,
            "error": run.error,
            "output": run.output,
            "finish_reason": run.finish_reason,
            "usage": Usage(
                tokens_in=run.tokens_in,
                tokens_out=run.tokens_out,
                total_tokens=run.tokens_in + run.tokens_out,
            ),
            "cost": run.cost,
            "latency_ms": run.latency_ms,
            "cache_hit": run.cache_hit,
            "created_at": run.created_at,
            "created_by": run.created_by,
        }
        if not detail:
            return RunOut(**values)
        return RunDetail(
            **values,
            variables=run.variables,
            messages=run.messages,
            params=run.params,
            llm_call_id=run.llm_call_id,
        )

    async def _load_for_run(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, number: int | None
    ) -> tuple[Prompt, PromptVersion]:
        async with self._session(actor, project_id) as s:
            prompt = await self._prompt(s, prompt_id)
            return prompt, await self._version(s, prompt, number)

    async def test(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, body: RunIn
    ) -> RunDetail:
        prompt, version = await self._load_for_run(actor, project_id, prompt_id, body.version)
        values, messages = await self._render(version, body.variables)
        run = await self._execute(
            actor,
            project_id,
            kind="test",
            model=body.model or version.model,
            messages=messages,
            params=version.model_params,
            values=values,
            prompt=prompt,
            version=version.version,
        )
        out = self._run_out(run)
        assert isinstance(out, RunDetail)
        return out

    async def playground(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, body: PlaygroundIn
    ) -> list[RunDetail]:
        """Same rendered prompt on 2 to 4 models in parallel; a failure stays in its own run."""
        prompt, version = await self._load_for_run(actor, project_id, prompt_id, body.version)
        values, messages = await self._render(version, body.variables)
        group = uuid7()
        runs = await asyncio.gather(
            *(
                self._execute(
                    actor,
                    project_id,
                    kind="playground",
                    model=model,
                    messages=messages,
                    params=version.model_params,
                    values=values,
                    prompt=prompt,
                    version=version.version,
                    group_id=group,
                )
                for model in body.models
            )
        )
        return [r for r in (self._run_out(run) for run in runs) if isinstance(r, RunDetail)]

    async def runs(
        self, actor: Actor, project_id: uuid.UUID, prompt_id: uuid.UUID, limit: int = 20
    ) -> list[RunOut]:
        async with self._session(actor, project_id) as s:
            await self._prompt(s, prompt_id)
            rows = await s.execute(
                select(PromptRun)
                .where(PromptRun.prompt_id == prompt_id)
                .order_by(PromptRun.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
            return [self._run_out(r, detail=False) for r in rows.scalars()]

    async def run(self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID) -> RunDetail:
        async with self._session(actor, project_id) as s:
            row = await s.get(PromptRun, run_id)
            if row is None:
                raise ProblemError(404, "not_found", "Run not found")
            out = self._run_out(row)
            assert isinstance(out, RunDetail)
            return out

    async def assist(self, actor: Actor, project_id: uuid.UUID, body: AssistIn) -> AssistOut:
        """Help Me Write on the project's `default-chat` alias, traced as an `assist` run."""
        run = await self._execute(
            actor,
            project_id,
            kind="assist",
            model=assist.ASSIST_MODEL,
            messages=assist.messages(body.description, body.framework),
            params={"temperature": 0.4},
            values={"description": body.description, "framework": body.framework or ""},
        )
        if run.status != "ok":
            raise ProblemError(
                502,
                run.error_code or "assist_failed",
                "Help Me Write could not reach the model",
                run.error,
            )
        draft = assist.parse(run.output or "", body.framework)
        if draft is None:
            raise ProblemError(
                502,
                "assist_invalid_output",
                "The model did not return a usable prompt; try Re-generate",
                {"run_id": str(run.id)},
            )
        return AssistOut(
            framework=draft.framework,
            recommended=body.framework is None,
            system_template=draft.system_template,
            user_template=draft.user_template,
            run_id=run.id,
        )
