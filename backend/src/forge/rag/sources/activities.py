"""Temporal side of scheduled sources: one workflow per sync, plus the scheduler that starts
them (E2.13, ADR-004). The workflow id is the source id, so a source never syncs twice at once."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from forge.core.db import admin_scope
    from forge.core.logging import get_logger
    from forge.rag.sources.sync import SyncEngine
    from forge.rag.starter import IngestStarter

log = get_logger(__name__)

SYNC_WORKFLOW = "SyncSource"
SCHEDULER_TICK_S = 30
CLAIM_BATCH = 20


class SourceActivities:
    def __init__(self, engine: SyncEngine) -> None:
        self._engine = engine

    @activity.defn(name="rag.sync_source")
    async def sync_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._engine.run(
            uuid.UUID(payload["project_id"]),
            uuid.UUID(payload["source_id"]),
            payload.get("trigger", "schedule"),
            allow_mass_delete=bool(payload.get("allow_mass_delete")),
            heartbeat=activity.heartbeat,
        )

    def all(self) -> list[Any]:
        return [self.sync_source]


@workflow.defn(name=SYNC_WORKFLOW)
class SyncSourceWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            "rag.sync_source",
            payload,
            start_to_close_timeout=timedelta(hours=6),
            heartbeat_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return result


SOURCE_WORKFLOWS = [SyncSourceWorkflow]


async def schedule_due(sessions: async_sessionmaker[AsyncSession], starter: IngestStarter) -> int:
    """Claim sources whose time has come and start their syncs. The claim moves `next_run_at`
    in the same statement, so two workers never start the same source."""
    async with admin_scope(sessions) as s:
        rows = (
            await s.execute(
                text(
                    """
                    UPDATE rag_source
                    SET next_run_at = now() + interval_minutes * interval '1 minute'
                    WHERE id IN (
                        SELECT id FROM rag_source
                        WHERE enabled AND interval_minutes IS NOT NULL AND next_run_at <= now()
                          AND (status = 'idle' OR updated_at < now() - interval '2 hours')
                        ORDER BY next_run_at LIMIT :n FOR UPDATE SKIP LOCKED)
                    RETURNING id, project_id
                    """
                ),
                {"n": CLAIM_BATCH},
            )
        ).all()
    for source_id, project_id in rows:
        await starter.source(project_id, source_id, "schedule")
    return len(rows)


async def scheduler_loop(
    sessions: async_sessionmaker[AsyncSession], starter: IngestStarter
) -> None:
    while True:
        try:
            started = await schedule_due(sessions, starter)
            if started:
                log.info("rag.sources_scheduled", count=started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("rag.source_scheduler_failed", error=str(exc)[:200])
        await asyncio.sleep(SCHEDULER_TICK_S)
