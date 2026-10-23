"""Triggers of use cases (E3.9): cron and interval schedules, and inbound tokens (webhook).

A trigger starts a run of the published version as the Builder who created it; the run shows
up in the normal run history with that user. Delivery is always asynchronous: the optional
`webhook_url` is called when the run ends (the M2.3d webhook, same egress rules).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.crypto import random_token, sha256
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.usecases.cron import Cron, CronError
from forge.usecases.models import UseCase, UseCaseTrigger
from forge.usecases.runs import UseCaseRuns
from forge.usecases.schemas import In, Out, RunStartIn

log = structlog.get_logger()

NOT_FOUND = ProblemError(404, "not_found", "Trigger not found")
TICK_S = 20
CLAIM_BATCH = 20
MAX_FAILURES = 5
TOKEN_PREFIX = "ft_"  # noqa: S105 - a public prefix, not a secret


class TriggerIn(In):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["cron", "interval", "webhook"]
    cron: str | None = Field(
        default=None, max_length=120, description="Five fields, e.g. `0 8 * * 1-5`"
    )
    interval_minutes: int | None = Field(default=None, ge=1, le=525_600)
    timezone: str = Field(default="UTC", max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = Field(default=None, max_length=2000)
    enabled: bool = True

    @model_validator(mode="after")
    def _shape(self) -> TriggerIn:
        check_schedule(self.kind, self.cron, self.interval_minutes, self.timezone)
        return self


class TriggerUpdateIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    cron: str | None = Field(default=None, max_length=120)
    interval_minutes: int | None = Field(default=None, ge=1, le=525_600)
    timezone: str | None = Field(default=None, max_length=64)
    inputs: dict[str, Any] | None = None
    webhook_url: str | None = Field(default=None, max_length=2000)
    clear_webhook: bool = False
    enabled: bool | None = None


class TriggerOut(Out):
    id: uuid.UUID
    use_case_id: uuid.UUID
    name: str
    kind: str
    cron: str | None
    interval_minutes: int | None
    timezone: str
    inputs: dict[str, Any]
    webhook_url: str | None
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: uuid.UUID | None
    last_status: str | None
    last_error: str | None
    failures: int
    created_at: datetime
    created_by: uuid.UUID
    token: str | None = Field(
        default=None, description="Shown once, when a webhook trigger is created or rotated"
    )


class FireIn(In):
    inputs: dict[str, Any] = Field(default_factory=dict)


class FireOut(Out):
    run_id: uuid.UUID
    status: str


def check_schedule(kind: str, cron: str | None, interval: int | None, tz: str) -> None:
    """Raise `ValueError` (so Pydantic reports a 422) when the schedule fields do not fit."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"unknown time zone {tz!r}") from exc
    if kind == "cron":
        if not cron:
            raise ValueError("a cron trigger needs `cron`")
        try:
            Cron(cron).next_after(utcnow(), tz)
        except CronError as exc:
            raise ValueError(str(exc)) from exc
    elif cron:
        raise ValueError("`cron` only applies to cron triggers")
    if kind == "interval" and not interval:
        raise ValueError("an interval trigger needs `interval_minutes`")
    if kind != "interval" and interval:
        raise ValueError("`interval_minutes` only applies to interval triggers")


def next_run(
    kind: str, cron: str | None, interval: int | None, tz: str, after: datetime
) -> datetime | None:
    if kind == "cron" and cron:
        return Cron(cron).next_after(after, tz)
    if kind == "interval" and interval:
        return after + timedelta(minutes=interval)
    return None


def _describe(exc: ProblemError) -> str:
    details = exc.detail if isinstance(exc.detail, list) else []
    parts = [str(d.get("message", "")) for d in details[:3] if isinstance(d, dict)]
    return "; ".join([exc.title, *[p for p in parts if p]])


class Triggers:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], runs: UseCaseRuns) -> None:
        self._sessions = sessions
        self._runs = runs

    # ------------------------------------------------------------------ CRUD
    @staticmethod
    def _out(row: UseCaseTrigger, token: str | None = None) -> TriggerOut:
        out = TriggerOut.model_validate(row)
        out.token = token
        return out

    @staticmethod
    async def _get(s: AsyncSession, trigger_id: uuid.UUID) -> UseCaseTrigger:
        row = await s.get(UseCaseTrigger, trigger_id)
        if row is None:
            raise NOT_FOUND
        return row

    async def list(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID | None = None
    ) -> list[TriggerOut]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            q = select(UseCaseTrigger).order_by(UseCaseTrigger.name)
            if use_case_id:
                q = q.where(UseCaseTrigger.use_case_id == use_case_id)
            return [self._out(r) for r in (await s.execute(q)).scalars()]

    async def create(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, body: TriggerIn
    ) -> TriggerOut:
        assert actor.id is not None
        token = TOKEN_PREFIX + random_token() if body.kind == "webhook" else None
        async with project_scope(self._sessions, project_id, actor.id) as s:
            if await s.get(UseCase, use_case_id) is None:
                raise ProblemError(404, "not_found", "Use case not found")
            row = UseCaseTrigger(
                project_id=project_id,
                use_case_id=use_case_id,
                name=body.name.strip(),
                kind=body.kind,
                cron=body.cron,
                interval_minutes=body.interval_minutes,
                timezone=body.timezone,
                token_hash=sha256(token) if token else None,
                inputs=body.inputs,
                webhook_url=body.webhook_url,
                enabled=body.enabled,
                next_run_at=next_run(
                    body.kind, body.cron, body.interval_minutes, body.timezone, utcnow()
                ),
                created_by=actor.id,
            )
            s.add(row)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A trigger with this name exists") from exc
            await audit.record(
                s,
                actor,
                "usecase.trigger.create",
                "use_case_trigger",
                row.id,
                {
                    "name": row.name,
                    "kind": row.kind,
                    "cron": row.cron,
                    "interval_minutes": row.interval_minutes,
                },
                project_id=project_id,
            )
            return self._out(row, token)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, trigger_id: uuid.UUID, body: TriggerUpdateIn
    ) -> TriggerOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            row = await self._get(s, trigger_id)
            fields = body.model_fields_set
            cron = body.cron if "cron" in fields else row.cron
            interval = (
                body.interval_minutes if "interval_minutes" in fields else row.interval_minutes
            )
            tz = body.timezone or row.timezone
            try:
                check_schedule(row.kind, cron, interval, tz)
            except ValueError as exc:
                raise ProblemError(422, "invalid_schedule", str(exc)) from exc
            if body.name is not None:
                row.name = body.name.strip()
            if body.inputs is not None:
                row.inputs = body.inputs
            if body.clear_webhook:
                row.webhook_url = None
            elif body.webhook_url is not None:
                row.webhook_url = body.webhook_url
            schedule_changed = (cron, interval, tz) != (
                row.cron,
                row.interval_minutes,
                row.timezone,
            )
            row.cron, row.interval_minutes, row.timezone = cron, interval, tz
            if body.enabled is not None:
                if body.enabled and not row.enabled:
                    row.failures = 0
                    schedule_changed = True
                row.enabled = body.enabled
            if schedule_changed and row.enabled:
                row.next_run_at = next_run(row.kind, cron, interval, tz, utcnow())
            if not row.enabled:
                row.next_run_at = None
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A trigger with this name exists") from exc
            await audit.record(
                s,
                actor,
                "usecase.trigger.update",
                "use_case_trigger",
                row.id,
                {"fields": sorted(fields)},
                project_id=project_id,
            )
            return self._out(row)

    async def delete(self, actor: Actor, project_id: uuid.UUID, trigger_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            row = await self._get(s, trigger_id)
            await audit.record(
                s,
                actor,
                "usecase.trigger.delete",
                "use_case_trigger",
                row.id,
                {"name": row.name},
                project_id=project_id,
            )
            await s.delete(row)

    async def rotate(
        self, actor: Actor, project_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> TriggerOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            row = await self._get(s, trigger_id)
            if row.kind != "webhook":
                raise ProblemError(422, "not_a_webhook", "Only webhook triggers have a token")
            token = TOKEN_PREFIX + random_token()
            row.token_hash = sha256(token)
            await audit.record(
                s,
                actor,
                "usecase.trigger.rotate",
                "use_case_trigger",
                row.id,
                {},
                project_id=project_id,
            )
            return self._out(row, token)

    # ------------------------------------------------------------------ firing
    async def _fire(self, row: UseCaseTrigger, extra: dict[str, Any]) -> uuid.UUID:
        """Start the run. Raises what `UseCaseRuns.start` raises."""
        actor = Actor(id=row.created_by, label=f"trigger:{row.id}")
        started = await self._runs.start(
            actor,
            row.project_id,
            row.use_case_id,
            RunStartIn(
                inputs={**row.inputs, **extra}, mode="published", webhook_url=row.webhook_url
            ),
            can_build=True,
            inspect=False,
        )
        return started.id

    async def _record(
        self,
        trigger_id: uuid.UUID,
        project_id: uuid.UUID,
        run_id: uuid.UUID | None,
        error: str | None,
    ) -> None:
        async with admin_scope(self._sessions) as s:
            row = await s.get(UseCaseTrigger, trigger_id)
            if row is None:
                return
            row.last_run_at = utcnow()
            if run_id:
                row.last_run_id, row.last_status, row.last_error, row.failures = (
                    run_id,
                    "started",
                    None,
                    0,
                )
            else:
                row.last_status, row.last_error = "failed", (error or "")[:500]
                row.failures += 1
                if row.failures >= MAX_FAILURES and row.kind != "webhook":
                    row.enabled, row.next_run_at = False, None
                    log.warning("usecase.trigger_disabled", trigger=str(row.id))

    async def _run(self, row: UseCaseTrigger, extra: dict[str, Any]) -> uuid.UUID:
        try:
            run_id = await self._fire(row, extra)
        except ProblemError as exc:
            await self._record(row.id, row.project_id, None, _describe(exc))
            raise
        except Exception as exc:
            await self._record(row.id, row.project_id, None, str(exc))
            raise
        await self._record(row.id, row.project_id, run_id, None)
        return run_id

    async def fire_now(
        self, actor: Actor, project_id: uuid.UUID, trigger_id: uuid.UUID, body: FireIn
    ) -> FireOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            row = await self._get(s, trigger_id)
            s.expunge(row)
        return FireOut(run_id=await self._run(row, body.inputs), status="queued")

    async def fire_token(self, token: str, body: FireIn) -> FireOut:
        async with admin_scope(self._sessions) as s:
            row = (
                await s.execute(
                    select(UseCaseTrigger).where(UseCaseTrigger.token_hash == sha256(token))
                )
            ).scalar_one_or_none()
            if row is None or not row.enabled or row.kind != "webhook":
                raise ProblemError(404, "not_found", "Unknown trigger")
            s.expunge(row)
        return FireOut(run_id=await self._run(row, body.inputs), status="queued")

    # ------------------------------------------------------------------ scheduler
    async def fire_due(self) -> int:
        """Claim due schedules (moving `next_run_at` in the same transaction, so replicas never
        fire one twice) and start them. A schedule that was due for longer than one period,
        e.g. after downtime, fires once, not once per missed slot."""
        now = utcnow()
        async with admin_scope(self._sessions) as s:
            rows = list(
                (
                    await s.execute(
                        select(UseCaseTrigger)
                        .where(
                            UseCaseTrigger.enabled,
                            UseCaseTrigger.kind != "webhook",
                            UseCaseTrigger.next_run_at <= now,
                        )
                        .order_by(UseCaseTrigger.next_run_at)
                        .limit(CLAIM_BATCH)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            for row in rows:
                row.next_run_at = next_run(
                    row.kind, row.cron, row.interval_minutes, row.timezone, now
                )
            s.expunge_all()
        for row in rows:
            try:
                await self._run(row, {})
            except Exception as exc:
                log.warning("usecase.trigger_failed", trigger=str(row.id), error=str(exc)[:200])
        return len(rows)

    async def loop(self) -> None:
        while True:
            try:
                if started := await self.fire_due():
                    log.info("usecase.triggers_fired", count=started)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("usecase.trigger_scheduler_failed", error=str(exc)[:200])
            await asyncio.sleep(TICK_S)
