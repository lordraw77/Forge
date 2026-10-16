"""Temporal worker processes (ADR-001, ADR-004).

Each worker polls one task queue. Domain modules register their workflows and activities
here as epics land (runs: use case and agent runs; ingest: RAG ingestion and re-sync).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

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


@dataclass(frozen=True)
class QueueRegistration:
    workflows: list[type[Any]]
    activities: list[Any]


def registrations(queue: str) -> QueueRegistration:
    if queue not in TASK_QUEUES:
        raise ValueError(f"unknown task queue {queue!r}, expected one of {TASK_QUEUES}")
    return QueueRegistration(workflows=[PingWorkflow], activities=[ping])


async def _heartbeat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(HEARTBEAT_FILE.touch)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)


async def run_worker(queue: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    reg = registrations(queue)
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with Worker(client, task_queue=queue, workflows=reg.workflows, activities=reg.activities):
        log.info("worker.started", queue=queue, temporal=settings.temporal_address)
        await _heartbeat(stop)
    log.info("worker.stopped", queue=queue)


def worker_is_alive() -> bool:
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime < HEARTBEAT_MAX_AGE_S
    except FileNotFoundError:
        return False
