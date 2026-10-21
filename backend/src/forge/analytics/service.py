"""Dashboards and activity log, read-only SQL over the records the platform already keeps.

Nothing here has a table of its own: the figures come from `llm_call`, `guardrail_event`,
`use_case_run`, `agent_run` and `audit_event`, so they reconcile with the per-run totals (AC9).
"""
# ruff: noqa: S608  (SQL is assembled only from constants in this module: scope, bucket, TOP)

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.settings_store import ActivityRetention, SettingsStore
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError

TOP = 10

Bucket = Literal["hour", "day"]


class Totals(BaseModel):
    calls: int = 0
    errors: int = 0
    cache_hits: int = 0
    fallbacks: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    ttft_p50_ms: float | None = None
    ttft_p95_ms: float | None = None


class SeriesPoint(BaseModel):
    t: datetime
    calls: int
    errors: int
    cost: float
    tokens: int
    latency_p95_ms: float | None = None


class ModelRow(BaseModel):
    model: str
    calls: int
    errors: int
    fallbacks: int
    tokens_in: int
    tokens_out: int
    cost: float
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    ttft_p50_ms: float | None = None


class UserRow(BaseModel):
    user_id: uuid.UUID | None
    label: str
    calls: int
    cost: float
    tokens: int


class NamedCount(BaseModel):
    name: str
    count: int
    extra: float | None = None


class GuardrailSummary(BaseModel):
    total: int = 0
    by_check: list[NamedCount] = []
    by_action: list[NamedCount] = []
    by_stage: list[NamedCount] = []
    series: list[NamedCount] = []


class Adoption(BaseModel):
    active_users: int = 0
    use_case_runs: int = 0
    use_case_failed: int = 0
    agent_runs: int = 0
    top_use_cases: list[NamedCount] = []
    top_agents: list[NamedCount] = []
    users_by_day: list[NamedCount] = []


class Reconciliation(BaseModel):
    """AC9: what agent runs recorded against what the gateway recorded for their steps."""

    run_cost: float
    call_cost: float
    run_tokens: int
    call_tokens: int


class ProjectDashboard(BaseModel):
    since: datetime
    until: datetime
    bucket: Bucket
    totals: Totals
    series: list[SeriesPoint]
    by_model: list[ModelRow]
    by_user: list[UserRow]
    by_endpoint: list[NamedCount]
    errors: list[NamedCount]
    guardrails: GuardrailSummary
    adoption: Adoption
    reconciliation: Reconciliation


class ProviderRow(BaseModel):
    provider: str
    calls: int
    errors: int
    cost: float


class InsightModelRow(ModelRow):
    projects: int
    cache_hits: int
    error_rate: float
    last_call_at: datetime | None = None


class ModelInsights(BaseModel):
    since: datetime
    until: datetime
    bucket: Bucket
    totals: Totals
    series: list[SeriesPoint]
    models: list[InsightModelRow]
    providers: list[ProviderRow]
    top_projects: list[NamedCount]
    unused_models: list[str]


class ActivityItem(BaseModel):
    at: datetime
    kind: str
    title: str
    detail: str = ""
    status: str | None = None
    actor: str | None = None
    project_id: uuid.UUID | None = None
    project_name: str | None = None
    ref_id: str | None = None


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def _fo(v: Any) -> float | None:
    return round(float(v), 1) if v is not None else None


def window(
    since: datetime | None, until: datetime | None, max_days: int
) -> tuple[datetime, datetime, Bucket]:
    now = datetime.now(UTC)
    until = until or now
    since = since or until - timedelta(days=7)
    if since >= until:
        raise ProblemError(422, "invalid_range", "'since' must be before 'until'")
    if until - since > timedelta(days=max_days):
        raise ProblemError(422, "invalid_range", f"The range is limited to {max_days} days")
    bucket: Bucket = "hour" if until - since <= timedelta(days=3) else "day"
    return since, until, bucket


_TOTALS = """
    SELECT count(*) AS calls,
           count(*) FILTER (WHERE status <> 'ok') AS errors,
           count(*) FILTER (WHERE cache_hit) AS cache_hits,
           count(*) FILTER (WHERE jsonb_array_length(attempts) > 1) AS fallbacks,
           coalesce(sum(tokens_in), 0) AS tokens_in,
           coalesce(sum(tokens_out), 0) AS tokens_out,
           coalesce(sum(cost), 0) AS cost,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) AS t50,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS t95
    FROM llm_call
    WHERE created_at >= :since AND created_at < :until {scope}
"""


def _totals(row: Any) -> Totals:
    return Totals(
        calls=row.calls,
        errors=row.errors,
        cache_hits=row.cache_hits,
        fallbacks=row.fallbacks,
        tokens_in=int(row.tokens_in),
        tokens_out=int(row.tokens_out),
        cost=_f(row.cost),
        latency_p50_ms=_fo(row.p50),
        latency_p95_ms=_fo(row.p95),
        ttft_p50_ms=_fo(row.t50),
        ttft_p95_ms=_fo(row.t95),
    )


async def _series(
    s: AsyncSession, scope: str, params: dict[str, Any], bucket: Bucket
) -> list[SeriesPoint]:
    rows = await s.execute(
        text(
            f"""
            SELECT date_trunc('{bucket}', created_at) AS bt, count(*) AS calls,
                   count(*) FILTER (WHERE status <> 'ok') AS errors,
                   coalesce(sum(cost), 0) AS cost,
                   coalesce(sum(tokens_in + tokens_out), 0) AS tokens,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
            FROM llm_call
            WHERE created_at >= :since AND created_at < :until {scope}
            GROUP BY 1 ORDER BY 1
            """
        ),
        params,
    )
    return [
        SeriesPoint(
            t=r.bt,
            calls=r.calls,
            errors=r.errors,
            cost=_f(r.cost),
            tokens=int(r.tokens),
            latency_p95_ms=_fo(r.p95),
        )
        for r in rows
    ]


_MODEL_COLUMNS = """
    count(*) AS calls,
    count(*) FILTER (WHERE status <> 'ok') AS errors,
    count(*) FILTER (WHERE jsonb_array_length(attempts) > 1) AS fallbacks,
    coalesce(sum(tokens_in), 0) AS tokens_in, coalesce(sum(tokens_out), 0) AS tokens_out,
    coalesce(sum(cost), 0) AS cost,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) AS t50
"""


class Analytics:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: SettingsStore) -> None:
        self._sessions = sessions
        self._store = store

    @property
    def retention_days(self) -> int:
        return self._store.get(ActivityRetention).days

    async def project_dashboard(
        self,
        project_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        since: datetime | None,
        until: datetime | None,
    ) -> ProjectDashboard:
        since, until, bucket = window(since, until, self.retention_days)
        p = {"since": since, "until": until, "project": project_id}
        scope = "AND project_id = :project"
        async with project_scope(self._sessions, project_id, actor_id) as s:
            totals = _totals((await s.execute(text(_TOTALS.format(scope=scope)), p)).one())
            series = await _series(s, scope, p, bucket)
            by_model = [
                ModelRow(
                    model=r.model,
                    calls=r.calls,
                    errors=r.errors,
                    fallbacks=r.fallbacks,
                    tokens_in=int(r.tokens_in),
                    tokens_out=int(r.tokens_out),
                    cost=_f(r.cost),
                    latency_p50_ms=_fo(r.p50),
                    latency_p95_ms=_fo(r.p95),
                    ttft_p50_ms=_fo(r.t50),
                )
                for r in await s.execute(
                    text(
                        f"""SELECT coalesce(model_name, requested) AS model, {_MODEL_COLUMNS}
                        FROM llm_call
                        WHERE created_at >= :since AND created_at < :until {scope}
                        GROUP BY 1 ORDER BY cost DESC, calls DESC LIMIT 50"""
                    ),
                    p,
                )
            ]
            by_user = [
                UserRow(
                    user_id=r.user_id,
                    label=r.label,
                    calls=r.calls,
                    cost=_f(r.cost),
                    tokens=int(r.tokens),
                )
                for r in await s.execute(
                    text(
                        f"""SELECT c.user_id,
                               coalesce(u.username, CASE WHEN c.api_key_id IS NOT NULL
                                   THEN 'API key ' || left(c.api_key_id::text, 8)
                                   ELSE 'system' END) AS label,
                               count(*) AS calls, coalesce(sum(c.cost), 0) AS cost,
                               coalesce(sum(c.tokens_in + c.tokens_out), 0) AS tokens
                        FROM llm_call c LEFT JOIN app_user u ON u.id = c.user_id
                        WHERE c.created_at >= :since AND c.created_at < :until
                          AND c.project_id = :project
                        GROUP BY c.user_id, c.api_key_id, u.username
                        ORDER BY cost DESC, calls DESC LIMIT {TOP}"""
                    ),
                    p,
                )
            ]
            by_endpoint = [
                NamedCount(name=r.endpoint, count=r.n, extra=_f(r.cost))
                for r in await s.execute(
                    text(
                        f"""SELECT endpoint, count(*) AS n, coalesce(sum(cost), 0) AS cost
                        FROM llm_call
                        WHERE created_at >= :since AND created_at < :until {scope}
                        GROUP BY 1 ORDER BY n DESC"""
                    ),
                    p,
                )
            ]
            errors = [
                NamedCount(name=r.code, count=r.n)
                for r in await s.execute(
                    text(
                        f"""SELECT coalesce(error_code, status) AS code, count(*) AS n
                        FROM llm_call
                        WHERE created_at >= :since AND created_at < :until {scope}
                          AND status <> 'ok'
                        GROUP BY 1 ORDER BY n DESC LIMIT {TOP}"""
                    ),
                    p,
                )
            ]
            guardrails = await self._guardrails(s, p, bucket)
            adoption = await self._adoption(s, p, bucket)
            reconciliation = await self._reconcile(s, p)
        return ProjectDashboard(
            since=since,
            until=until,
            bucket=bucket,
            totals=totals,
            series=series,
            by_model=by_model,
            by_user=by_user,
            by_endpoint=by_endpoint,
            errors=errors,
            guardrails=guardrails,
            adoption=adoption,
            reconciliation=reconciliation,
        )

    async def _guardrails(
        self, s: AsyncSession, p: dict[str, Any], bucket: Bucket
    ) -> GuardrailSummary:
        where = "created_at >= :since AND created_at < :until AND project_id = :project"

        async def grouped(column: str) -> list[NamedCount]:
            rows = await s.execute(
                text(
                    f"SELECT {column} AS k, count(*) AS n FROM guardrail_event WHERE {where}"
                    " GROUP BY 1 ORDER BY n DESC"
                ),
                p,
            )
            return [NamedCount(name=r.k, count=r.n) for r in rows]

        series = await s.execute(
            text(
                f"""SELECT date_trunc('{bucket}', created_at) AS bt, count(*) AS n
                FROM guardrail_event WHERE {where} GROUP BY 1 ORDER BY 1"""
            ),
            p,
        )
        by_check = await grouped("check_type")
        return GuardrailSummary(
            total=sum(c.count for c in by_check),
            by_check=by_check,
            by_action=await grouped("action"),
            by_stage=await grouped("stage"),
            series=[NamedCount(name=r.bt.isoformat(), count=r.n) for r in series],
        )

    async def _adoption(self, s: AsyncSession, p: dict[str, Any], bucket: Bucket) -> Adoption:
        w = "created_at >= :since AND created_at < :until AND project_id = :project"
        users = (
            await s.execute(
                text(
                    f"""SELECT count(DISTINCT u) FROM (
                        SELECT user_id AS u FROM llm_call WHERE {w} AND user_id IS NOT NULL
                        UNION SELECT created_by FROM use_case_run
                              WHERE {w} AND created_by IS NOT NULL
                        UNION SELECT created_by FROM agent_run
                              WHERE {w} AND created_by IS NOT NULL) x"""
                ),
                p,
            )
        ).scalar_one()
        uc = (
            await s.execute(
                text(
                    f"""SELECT count(*) AS n, count(*) FILTER (WHERE status = 'failed') AS failed
                    FROM use_case_run WHERE {w} AND mode = 'published'"""
                ),
                p,
            )
        ).one()
        agent_runs = (
            await s.execute(text(f"SELECT count(*) FROM agent_run WHERE {w}"), p)
        ).scalar_one()
        top_uc = await s.execute(
            text(
                f"""SELECT use_case_name AS k, count(*) AS n FROM use_case_run
                WHERE {w} AND mode = 'published' GROUP BY 1 ORDER BY n DESC LIMIT {TOP}"""
            ),
            p,
        )
        top_ag = await s.execute(
            text(
                f"""SELECT a.name AS k, count(*) AS n FROM agent_run r
                JOIN agent a ON a.id = r.agent_id
                WHERE r.created_at >= :since AND r.created_at < :until AND r.project_id = :project
                GROUP BY 1 ORDER BY n DESC LIMIT {TOP}"""
            ),
            p,
        )
        per_day = await s.execute(
            text(
                f"""SELECT date_trunc('day', t) AS d, count(DISTINCT u) AS n FROM (
                    SELECT created_at AS t, user_id AS u FROM llm_call
                      WHERE {w} AND user_id IS NOT NULL
                    UNION ALL SELECT created_at, created_by FROM use_case_run
                      WHERE {w} AND created_by IS NOT NULL) x GROUP BY 1 ORDER BY 1"""
            ),
            p,
        )
        return Adoption(
            active_users=users,
            use_case_runs=uc.n,
            use_case_failed=uc.failed,
            agent_runs=agent_runs,
            top_use_cases=[NamedCount(name=r.k, count=r.n) for r in top_uc],
            top_agents=[NamedCount(name=r.k, count=r.n) for r in top_ag],
            users_by_day=[NamedCount(name=r.d.date().isoformat(), count=r.n) for r in per_day],
        )

    async def _reconcile(self, s: AsyncSession, p: dict[str, Any]) -> Reconciliation:
        """AC9: agent run totals against the gateway calls their steps point at.

        Use case runs are not added: their totals already contain their agent runs.
        """
        row = (
            await s.execute(
                text(
                    """SELECT coalesce(sum(r.cost), 0) AS cost,
                       coalesce(sum(r.tokens_in + r.tokens_out), 0) AS tokens
                    FROM agent_run r WHERE r.created_at >= :since AND r.created_at < :until
                      AND r.project_id = :project AND r.status IN ('succeeded', 'failed')"""
                ),
                p,
            )
        ).one()
        calls = (
            await s.execute(
                text(
                    """SELECT coalesce(sum(c.cost), 0) AS cost,
                       coalesce(sum(c.tokens_in + c.tokens_out), 0) AS tokens
                    FROM llm_call c WHERE c.project_id = :project AND c.id IN (
                        SELECT st.llm_call_id FROM run_step st JOIN agent_run r ON r.id = st.run_id
                        WHERE r.created_at >= :since AND r.created_at < :until
                          AND r.project_id = :project AND r.status IN ('succeeded', 'failed')
                          AND st.llm_call_id IS NOT NULL)"""
                ),
                p,
            )
        ).one()
        return Reconciliation(
            run_cost=_f(row.cost),
            call_cost=_f(calls.cost),
            run_tokens=int(row.tokens),
            call_tokens=int(calls.tokens),
        )

    # ---- platform-wide (Super Admin)
    async def model_insights(
        self, actor_id: uuid.UUID, since: datetime | None, until: datetime | None
    ) -> ModelInsights:
        since, until, bucket = window(since, until, self.retention_days)
        p = {"since": since, "until": until}
        async with admin_scope(self._sessions, actor_id) as s:
            totals = _totals((await s.execute(text(_TOTALS.format(scope="")), p)).one())
            series = await _series(s, "", p, bucket)
            models = [
                InsightModelRow(
                    model=r.model,
                    calls=r.calls,
                    errors=r.errors,
                    fallbacks=r.fallbacks,
                    tokens_in=int(r.tokens_in),
                    tokens_out=int(r.tokens_out),
                    cost=_f(r.cost),
                    latency_p50_ms=_fo(r.p50),
                    latency_p95_ms=_fo(r.p95),
                    ttft_p50_ms=_fo(r.t50),
                    projects=r.projects,
                    cache_hits=r.cache_hits,
                    error_rate=round(r.errors / r.calls, 4) if r.calls else 0.0,
                    last_call_at=r.last_call,
                )
                for r in await s.execute(
                    text(
                        f"""SELECT coalesce(model_name, requested) AS model, {_MODEL_COLUMNS},
                               count(DISTINCT project_id) AS projects,
                               count(*) FILTER (WHERE cache_hit) AS cache_hits,
                               max(created_at) AS last_call
                        FROM llm_call WHERE created_at >= :since AND created_at < :until
                        GROUP BY 1 ORDER BY cost DESC, calls DESC LIMIT 200"""
                    ),
                    p,
                )
            ]
            providers = [
                ProviderRow(provider=r.provider, calls=r.calls, errors=r.errors, cost=_f(r.cost))
                for r in await s.execute(
                    text(
                        """SELECT coalesce(pr.name, 'unknown') AS provider, count(*) AS calls,
                               count(*) FILTER (WHERE c.status <> 'ok') AS errors,
                               coalesce(sum(c.cost), 0) AS cost
                        FROM llm_call c LEFT JOIN model m ON m.id = c.model_id
                        LEFT JOIN model_provider pr ON pr.id = m.provider_id
                        WHERE c.created_at >= :since AND c.created_at < :until
                        GROUP BY 1 ORDER BY cost DESC"""
                    ),
                    p,
                )
            ]
            top_projects = [
                NamedCount(name=r.name, count=r.n, extra=_f(r.cost))
                for r in await s.execute(
                    text(
                        f"""SELECT pj.name, count(*) AS n, coalesce(sum(c.cost), 0) AS cost
                        FROM llm_call c JOIN project pj ON pj.id = c.project_id
                        WHERE c.created_at >= :since AND c.created_at < :until
                        GROUP BY 1 ORDER BY cost DESC, n DESC LIMIT {TOP}"""
                    ),
                    p,
                )
            ]
            unused = [
                r.name
                for r in await s.execute(
                    text(
                        """SELECT m.name FROM model m
                        WHERE NOT EXISTS (SELECT 1 FROM llm_call c WHERE c.model_id = m.id
                              AND c.created_at >= :since AND c.created_at < :until)
                        ORDER BY m.name LIMIT 200"""
                    ),
                    p,
                )
            ]
        return ModelInsights(
            since=since,
            until=until,
            bucket=bucket,
            totals=totals,
            series=series,
            models=models,
            providers=providers,
            top_projects=top_projects,
            unused_models=unused,
        )

    # ---- activity (E2.14)
    async def project_activity(
        self,
        project_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        since: datetime | None,
        q: str | None,
        limit: int,
    ) -> list[ActivityItem]:
        floor = datetime.now(UTC) - timedelta(days=self.retention_days)
        since = max(since, floor) if since else floor
        async with project_scope(self._sessions, project_id, actor_id) as s:
            rows = await s.execute(
                text(
                    """
                    SELECT * FROM (
                      SELECT a.created_at AS at, 'admin' AS kind, a.action AS title,
                             a.target_type || coalesce(' ' || a.target_id, '') AS detail,
                             NULL AS status, a.actor_label AS actor, a.target_id AS ref_id
                      FROM audit_event a WHERE a.project_id = :project
                      UNION ALL
                      SELECT r.created_at, 'use_case_run', r.use_case_name,
                             r.mode || coalesce(' v' || r.version, ''), r.status,
                             u.username, r.id::text
                      FROM use_case_run r LEFT JOIN app_user u ON u.id = r.created_by
                      WHERE r.project_id = :project
                      UNION ALL
                      SELECT r.created_at, 'agent_run', ag.name, left(r.input, 120), r.status,
                             u.username, r.id::text
                      FROM agent_run r JOIN agent ag ON ag.id = r.agent_id
                      LEFT JOIN app_user u ON u.id = r.created_by
                      WHERE r.project_id = :project
                    ) x
                    WHERE at >= :since
                      AND (:q = '' OR title ILIKE :like OR detail ILIKE :like
                           OR coalesce(actor, '') ILIKE :like)
                    ORDER BY at DESC LIMIT :limit
                    """
                ),
                {
                    "project": project_id,
                    "since": since,
                    "q": q or "",
                    "like": f"%{(q or '').replace('%', '').replace('_', '')}%",
                    "limit": min(limit, 500),
                },
            )
            return [
                ActivityItem(
                    at=r.at,
                    kind=r.kind,
                    title=r.title,
                    detail=r.detail or "",
                    status=r.status,
                    actor=r.actor,
                    ref_id=r.ref_id,
                    project_id=project_id,
                )
                for r in rows
            ]

    async def my_activity(self, user_id: uuid.UUID, days: int, limit: int) -> list[ActivityItem]:
        """The caller's own runs across projects, for the home page (US-1.4.2)."""
        since = datetime.now(UTC) - timedelta(days=min(max(days, 1), self.retention_days))
        async with admin_scope(self._sessions, user_id) as s:
            rows = await s.execute(
                text(
                    """
                    SELECT * FROM (
                      SELECT r.created_at AS at, 'use_case_run' AS kind, r.use_case_name AS title,
                             r.mode AS detail, r.status, r.project_id, r.id::text AS ref_id
                      FROM use_case_run r WHERE r.created_by = :user
                      UNION ALL
                      SELECT r.created_at, 'agent_run', ag.name, left(r.input, 120), r.status,
                             r.project_id, r.id::text
                      FROM agent_run r JOIN agent ag ON ag.id = r.agent_id
                      WHERE r.created_by = :user
                    ) x JOIN project pj ON pj.id = x.project_id
                    WHERE at >= :since ORDER BY at DESC LIMIT :limit
                    """
                ),
                {"user": user_id, "since": since, "limit": min(limit, 200)},
            )
            return [
                ActivityItem(
                    at=r.at,
                    kind=r.kind,
                    title=r.title,
                    detail=r.detail or "",
                    status=r.status,
                    project_id=r.project_id,
                    project_name=r.name,
                    ref_id=r.ref_id,
                )
                for r in rows
            ]
