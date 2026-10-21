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
import uuid
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
from forge.observability.service import setup, start_metrics_server, temporal_interceptors

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


async def build_runs(reg: QueueRegistration, settings: Settings) -> QueueRegistration:
    """`runs` executes agent runs (E1.18): the LangGraph loop with checkpoints, the gateway,
    retriever tools and the event stream. No Docling and no object storage."""
    import redis.asyncio as redis
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from forge.admin.service import SettingsStore, listen_dsn
    from forge.agents.service import (
        WORKFLOWS as AGENT_WORKFLOWS,
    )
    from forge.agents.service import (
        AgentActivities,
        AgentRuntime,
        RunEvents,
        RuntimeDeps,
        checkpoint_pool,
        purge_checkpoints,
    )
    from forge.core.crypto import Envelope
    from forge.core.db import create_engine, session_factory
    from forge.gateway.service import Gateway
    from forge.prompts.service import PromptStudio
    from forge.rag.service import IngestStarter, RagStudio, Retriever, SearchClient
    from forge.tenancy.service import TenancyService
    from forge.tools.service import ToolStudio

    if settings.master_key is None:
        raise RuntimeError("FORGE_MASTER_KEY is required by the runs worker")
    engine = create_engine(settings)
    sessions = session_factory(engine)
    store = SettingsStore(sessions, listen_dsn(settings.database_url.get_secret_value()))
    await store.start()
    cache = redis.from_url(settings.redis_url)
    envelope = Envelope.from_master_key(settings.master_key.get_secret_value())
    gateway = Gateway.build(sessions, envelope, store, cache)
    await gateway.start(store)
    search = SearchClient(settings)
    prompts = PromptStudio(sessions, gateway)
    tenancy = TenancyService(sessions)
    rag = RagStudio(
        sessions,
        gateway,
        prompts,
        Retriever(sessions, search, gateway),
        IngestStarter(settings),
        tenancy.acl_terms,
    )
    runtime_tools = ToolStudio(sessions, envelope, settings)
    pool = checkpoint_pool(settings)
    await pool.open(wait=True)
    runtime = AgentRuntime(
        RuntimeDeps(
            sessions=sessions,
            gateway=gateway,
            prompts=prompts,
            rag=rag,
            events=RunEvents(cache),
            checkpointer=AsyncPostgresSaver(pool),  # type: ignore[arg-type]
            allow_private_hosts=settings.agent_allow_private_hosts,
            tools=runtime_tools,
        )
    )

    async def purge_loop() -> None:
        while True:  # checkpoints of runs that ended a week ago (ADR-004)
            try:
                removed = await purge_checkpoints(sessions)
                log.info("worker.checkpoints_purged", rows=removed)
            except Exception as exc:
                log.warning("worker.checkpoint_purge_failed", error=str(exc)[:200])
            await asyncio.sleep(6 * 3600)

    purger = asyncio.create_task(purge_loop())

    async def stop_purger() -> None:
        purger.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await purger

    from forge.usecases.activities import WORKFLOWS as USECASE_WORKFLOWS
    from forge.usecases.activities import UseCaseActivities, UseCaseRuntime

    reg.workflows += AGENT_WORKFLOWS + USECASE_WORKFLOWS
    reg.activities += AgentActivities(runtime).all()
    from forge.rag.service import ObjectStore
    from forge.tenancy.service import TenancyService
    from forge.usecases.approvals import ApprovalRuntime
    from forge.usecases.files import UseCaseFiles
    from forge.usecases.mail import Mailer

    reg.activities += UseCaseActivities(
        UseCaseRuntime(
            sessions,
            runtime,
            runtime_tools,
            prompts,
            UseCaseFiles(sessions, ObjectStore(settings)),
            ApprovalRuntime(
                sessions,
                TenancyService(sessions),
                Mailer(settings, store, sessions, envelope),
                settings,
                runtime_tools,
            ),
            gateway.guardrails,
        )
    ).all()
    reg.closers += [
        stop_purger,
        gateway.stop,
        store.stop,
        search.aclose,
        cache.aclose,
        pool.close,
        engine.dispose,
    ]
    return reg


async def _start_sources(
    reg: QueueRegistration,
    settings: Settings,
    sessions: Any,
    objects: Any,
    search: Any,
) -> Callable[[], Awaitable[None]]:
    """Scheduled RAG sources (E2.13): the sync workflow and the loop that starts due syncs."""
    from types import SimpleNamespace

    from forge.core.context import SYSTEM
    from forge.core.crypto import Envelope
    from forge.rag.collections import CollectionService
    from forge.rag.service import roots_for, source_roots
    from forge.rag.sources.activities import (
        SOURCE_WORKFLOWS,
        SourceActivities,
        scheduler_loop,
    )
    from forge.rag.sources.connectors import SourceContext
    from forge.rag.sources.sync import SyncEngine
    from forge.rag.starter import IngestStarter
    from forge.tools.service import ToolStudio

    assert settings.master_key is not None
    envelope = Envelope.from_master_key(settings.master_key.get_secret_value())
    tools = ToolStudio(sessions, envelope, settings)
    starter = IngestStarter(settings)
    roots = source_roots(settings)

    async def no_principals(_project_id: uuid.UUID) -> Any:
        return SimpleNamespace(groups={}, roles={})

    collections = CollectionService(sessions, objects, search, starter, no_principals)

    async def context(project_id: uuid.UUID, secret: dict[str, Any]) -> SourceContext:
        hosts, allow_private = await tools.get_policy(SYSTEM, project_id)

        async def rows(ds: str, query: str, limit: int) -> Any:
            return await tools.read_rows(project_id, ds, query, limit)

        return SourceContext(hosts, allow_private, secret, roots_for(roots, project_id), rows)

    reg.workflows += SOURCE_WORKFLOWS
    reg.activities += SourceActivities(SyncEngine(sessions, collections, envelope, context)).all()
    task = asyncio.create_task(scheduler_loop(sessions, starter))

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop


async def build_registrations(queue: str, settings: Settings) -> QueueRegistration:
    """`runs` runs agents (E1.18); `ingest` runs RAG ingestion (E1.15), which needs the
    database, object storage, OpenSearch and the gateway (embeddings) in this process."""
    reg = registrations(queue)
    if queue == "runs":
        return await build_runs(reg, settings)
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
    scheduler = await _start_sources(reg, settings, sessions, objects, search)
    reg.closers += [
        scheduler,
        gateway.stop,
        store.stop,
        search.aclose,
        cache.aclose,
        engine.dispose,
    ]
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
        # ADR-004 rule 4: the SDK default (10 s) delays recovery after a worker crash.
        sticky_queue_schedule_to_start_timeout=timedelta(seconds=3),
        graceful_shutdown_timeout=timedelta(seconds=15),
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
    setup(f"forge-worker-{queue}", settings)
    start_metrics_server(settings.metrics_port)
    reg = await build_registrations(queue, settings)
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=temporal_interceptors(),
    )
    task_queue = {
        "ingest": settings.temporal_ingest_queue,
        "runs": settings.temporal_runs_queue,
    }.get(queue, queue)
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
