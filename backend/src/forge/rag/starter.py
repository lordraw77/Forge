"""Starts ingestion workflows on Temporal from the API (ADR-004). The client connects lazily,
so the API starts even when Temporal is not up yet; a failed start leaves the document
`pending` and Retry starts it again."""

from __future__ import annotations

import asyncio
import uuid

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from forge.core.logging import get_logger
from forge.core.settings import Settings

log = get_logger(__name__)

DOCUMENT_WORKFLOW = "IngestDocument"
KB_WORKFLOW = "IndexKnowledgeBase"


class IngestStarter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    @property
    def queue(self) -> str:
        return self._settings.temporal_ingest_queue

    async def client(self) -> Client:
        async with self._lock:
            if self._client is None:
                self._client = await Client.connect(
                    self._settings.temporal_address, namespace=self._settings.temporal_namespace
                )
            return self._client

    async def _start(self, workflow: str, workflow_id: str, payload: dict[str, str]) -> bool:
        try:
            client = await self.client()
            await client.start_workflow(
                workflow,
                payload,
                id=workflow_id,
                task_queue=self.queue,
                id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
            )
            return True
        except Exception as exc:  # Temporal down: the caller reports `pending`
            log.error(
                "rag.workflow_start_failed", workflow=workflow, id=workflow_id, error=str(exc)
            )
            return False

    async def document(self, project_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        return await self._start(
            DOCUMENT_WORKFLOW,
            f"ingest-doc-{document_id}",
            {"project_id": str(project_id), "document_id": str(document_id)},
        )

    async def knowledge_base(self, project_id: uuid.UUID, kb_id: uuid.UUID) -> bool:
        return await self._start(
            KB_WORKFLOW,
            f"index-kb-{kb_id}",
            {"project_id": str(project_id), "kb_id": str(kb_id)},
        )
