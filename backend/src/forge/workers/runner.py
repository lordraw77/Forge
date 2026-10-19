"""Temporal worker processes (ADR-001, ADR-004).

Each worker polls one task queue. Domain modules register their workflows and activities
here as epics land (runs: use case and agent runs; ingest: RAG ingestion and re-sync).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from forge.core.logging import configure_logging, get_logger
from forge.core.settings import Settings, get_settings

log = get_logger(__name__)

TASK_QUEUES = ("runs", "ingest")
HEARTBEAT_FILE = Path("/tmp/forge-worker-alive")  # noqa: S108 - container-local liveness marker
HEARTBEAT_INTERVAL_S = 10
HEARTBEAT_MAX_AGE_S = 30


@activity.defn
async def ping(message: str) -> str:
    return f"pong: {message}"


@workflow.defn
class PingWorkflow:
    """Smoke-test workflow: proves a worker on this queue is polling and can run activities."""

    @workflow.run
    async def run(self, message: str) -> str:
        return await workflow.execute_activity(
            ping, message, start_to_close_timeout=timedelta(seconds=10)
        )


@dataclass
class QueueRegistration:
    workflows: list[type[Any]]
    activities: list[Any]
    closers: list[Callable[[], Awaitable[Any]]] = field(default_factory=list)
    warming: Any = None  # Docling model loading started at worker start (ingest)


def registrations(queue: str) -> QueueRegistration:
    """Workflows and activities that need no services (both queues answer the ping)."""
    if queue not in TASK_QUEUES:
        raise ValueError(f"unknown task queue {queue!r}, expected one of {TASK_QUEUES}")
    return QueueRegistration(workflows=[PingWorkflow], activities=[ping])


async def build_registrations(queue: str, settings: Settings) -> QueueRegistration:
    """`ingest` also runs RAG ingestion (E1.15), which needs the database, object storage,
    OpenSearch and the gateway (embeddings) in this process."""
    reg = registrations(queue)
    if queue != "ingest":
        return reg
    import redis.asyncio as redis

    from forge.admin.service import SettingsStore, listen_dsn
    from forge.core.crypto import Envelope
    from forge.core.db import create_engine, session_factory
    from forge.gateway.service import Gateway
    from forge.rag.service import WORKFLOWS, IngestActivities, IngestDeps, ObjectStore, SearchClient

    if settings.master_key is None:
        raise RuntimeError("FORGE_MASTER_KEY is required by the ingest worker")
    engine = create_engine(settings)
    sessions = session_factory(engine)
    store = SettingsStore(sessions, listen_dsn(settings.database_url.get_secret_value()))
    await store.start()
    cache = redis.from_url(settings.redis_url)
    gateway = Gateway.build(
        sessions, Envelope.from_master_key(settings.master_key.get_secret_value()), store, cache
    )
    await gateway.start(store)
    search = SearchClient(settings)
    objects = ObjectStore(settings)
    await objects.ensure_bucket()
    await search.ensure_index()
    activities = IngestActivities(IngestDeps(sessions, objects, search, gateway))
    # `-slim` image: download the Docling models into the volume on the first start (blocking,
    # so no document is parsed without them). Then load them in the background so the first
    # upload does not wait.
    from forge.rag.parsing import warm_up
    from forge.rag.service import docling_models_present, download_docling_models

    if os.environ.get("FORGE_MODELS_MODE") == "download" and not docling_models_present():
        try:
            log.info("worker.models_download", target=os.environ.get("DOCLING_ARTIFACTS_PATH"))
            await asyncio.to_thread(download_docling_models)
        except Exception as exc:  # Docling absent (API image) or network: parse errors say why
            log.error("worker.models_download_failed", error=str(exc)[:300])

    reg.warming = asyncio.get_running_loop().run_in_executor(None, warm_up)
    reg.workflows += WORKFLOWS
    reg.activities += activities.all()
    reg.closers += [gateway.stop, store.stop, search.aclose, cache.aclose, engine.dispose]
    return reg


# Workflow code is re-imported in Temporal's sandbox. Forge's own modules and the service
# libraries they import are passed through: workflows only call activities, and these libraries
# touch `random` or I/O at import time, which the sandbox forbids.
PASSTHROUGH = (
    "forge",
    "sqlalchemy",
    "pydantic",
    "pydantic_core",
    "pydantic_settings",
    "structlog",
    "httpx",
    "boto3",
    "botocore",
    "redis",
    "asyncpg",
    "litellm",
    "pgvector",
    "jinja2",
)


def new_worker(client: Client, task_queue: str, reg: QueueRegistration) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=reg.workflows,
        activities=reg.activities,
        max_concurrent_activities=8,
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(*PASSTHROUGH)
        ),
    )


async def _heartbeat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(HEARTBEAT_FILE.touch)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)


async def run_worker(queue: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    reg = await build_registrations(queue, settings)
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    task_queue = settings.temporal_ingest_queue if queue == "ingest" else queue
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        async with new_worker(client, task_queue, reg):
            log.info("worker.started", queue=task_queue, temporal=settings.temporal_address)
            await _heartbeat(stop)
    finally:
        for close in reg.closers:
            with contextlib.suppress(Exception):
                await close()
    log.info("worker.stopped", queue=task_queue)


def worker_is_alive() -> bool:
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime < HEARTBEAT_MAX_AGE_S
    except FileNotFoundError:
        return False
