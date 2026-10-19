"""Activities of the agent run workflow and the checkpoint pool they use (ADR-004).

`run_agent(run_id)` builds the LangGraph graph of the run's pinned agent version and runs it with
`thread_id = "run:<run_id>"`. A retried attempt continues from the last checkpoint instead of
starting over. A background heartbeat (every 2 s) lets Temporal notice a dead worker within the
workflow's heartbeat timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from temporalio import activity

from forge.agents.runtime import AgentRuntime
from forge.agents.workflow import FAIL_ACTIVITY, RUN_ACTIVITY, AgentRunWorkflow
from forge.core.settings import Settings
from forge.observability.service import metrics

WORKFLOWS = [AgentRunWorkflow]
HEARTBEAT_EVERY_S = 2


def checkpoint_dsn(settings: Settings) -> str:
    """The database URL in psycopg form (the SQLAlchemy one names the asyncpg driver)."""
    url = settings.database_url.get_secret_value()
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def checkpoint_pool(settings: Settings) -> AsyncConnectionPool:
    """Pool for `AsyncPostgresSaver` (autocommit, dict rows). Connections switch to the
    row-level-security role and use schema `langgraph` (checkpoints carry no project id: they
    are reached only by the thread id of a run that was already authorised)."""
    role = settings.db_app_role
    if not role.replace("_", "").isalnum():
        raise ValueError(f"invalid database role name: {role!r}")

    async def configure(conn: Any) -> None:
        await conn.execute(f'SET ROLE "{role}"')

    return AsyncConnectionPool(
        checkpoint_dsn(settings),
        min_size=1,
        max_size=settings.db_pool_size,
        open=False,
        configure=configure,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=langgraph",
        },
    )


class AgentActivities:
    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    def all(self) -> list[Any]:
        return [self.run_agent, self.fail_run]

    @activity.defn(name=RUN_ACTIVITY)
    async def run_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        metrics.ACTIVITIES_INFLIGHT.labels("run_agent").inc()

        async def beat() -> None:
            while True:
                activity.heartbeat()
                await asyncio.sleep(HEARTBEAT_EVERY_S)

        heartbeat = asyncio.create_task(beat())
        try:
            return await self._runtime.execute(
                uuid.UUID(payload["project_id"]),
                uuid.UUID(payload["run_id"]),
                activity.info().attempt,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            metrics.ACTIVITIES_INFLIGHT.labels("run_agent").dec()

    @activity.defn(name=FAIL_ACTIVITY)
    async def fail_run(self, payload: dict[str, Any]) -> None:
        await self._runtime.fail(
            uuid.UUID(payload["project_id"]),
            uuid.UUID(payload["run_id"]),
            str(payload.get("code", "worker_failed")),
            str(payload.get("message", "The run could not be completed")),
        )
