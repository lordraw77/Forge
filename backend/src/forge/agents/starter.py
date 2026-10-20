"""Starts agent run workflows on Temporal from the API (ADR-004). The client connects lazily,
so the API starts even when Temporal is not up yet; a failed start fails the run with
`queue_unavailable`."""

from __future__ import annotations

import asyncio
import uuid

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from forge.core.logging import get_logger
from forge.core.settings import Settings
from forge.observability.service import temporal_interceptors

log = get_logger(__name__)

RUN_WORKFLOW = "AgentRunWorkflow"


class RunStarter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> Client:
        async with self._lock:
            if self._client is None:
                self._client = await Client.connect(
                    self._settings.temporal_address,
                    namespace=self._settings.temporal_namespace,
                    interceptors=temporal_interceptors(),
                )
            return self._client

    async def start(
        self, project_id: uuid.UUID, run_id: uuid.UUID, workflow_id: str, timeout_s: int
    ) -> bool:
        try:
            client = await self.client()
            await client.start_workflow(
                RUN_WORKFLOW,
                {"project_id": str(project_id), "run_id": str(run_id), "timeout_s": timeout_s},
                id=workflow_id,
                task_queue=self._settings.temporal_runs_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
            return True
        except Exception as exc:  # Temporal down: the caller fails the run
            log.error("agents.workflow_start_failed", id=workflow_id, error=str(exc))
            return False

    async def start_named(
        self, workflow: str, payload: dict[str, object], workflow_id: str
    ) -> bool:
        """Start any workflow of the runs queue (use case runs)."""
        try:
            client = await self.client()
            await client.start_workflow(
                workflow,
                payload,
                id=workflow_id,
                task_queue=self._settings.temporal_runs_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
            return True
        except Exception as exc:
            log.error("workflow.start_failed", id=workflow_id, error=str(exc))
            return False

    async def cancel(self, workflow_id: str) -> None:
        """Ask Temporal to cancel a workflow; a finished or unknown one is ignored."""
        try:
            client = await self.client()
            await client.get_workflow_handle(workflow_id).cancel()
        except Exception as exc:
            log.warning("workflow.cancel_failed", id=workflow_id, error=str(exc)[:200])
