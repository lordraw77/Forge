"""Temporal side of ADR-004: the run lifecycle and HITL wait; the agent runs in an activity."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

TASK_QUEUE = "spike-e05a"

# Set by the worker process at start-up (one checkpointer and pool per process).
CHECKPOINTER: Any = None
HEARTBEAT_TIMEOUT = timedelta(seconds=10)


@dataclass
class AgentNodeInput:
    thread_id: str
    task: str
    resume: dict[str, Any] | None = None


@dataclass
class AgentNodeResult:
    status: str
    answer: str | None = None
    payload: dict[str, Any] | None = None
    start_mode: str = ""
    attempt: int = 0


@dataclass
class RunResult:
    answer: str | None
    activity_results: list[AgentNodeResult] = field(default_factory=list)


@activity.defn
async def run_agent_node(inp: AgentNodeInput) -> AgentNodeResult:
    # Imported here so the workflow sandbox never loads LangGraph or psycopg.
    from spikes.e05a import store
    from spikes.e05a.agent import run_agent

    attempt = activity.info().attempt
    await store.record(inp.thread_id, "activity_attempt", str(attempt))

    async def heartbeat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(2)

    beat = asyncio.create_task(heartbeat())
    try:
        out = await run_agent(CHECKPOINTER, inp.thread_id, inp.task, inp.resume)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
    return AgentNodeResult(out.status, out.answer, out.payload, out.start_mode, attempt)


@workflow.defn
class AgentRunWorkflow:
    def __init__(self) -> None:
        self._status = "running"
        self._payload: dict[str, Any] | None = None
        self._decision: dict[str, Any] | None = None

    @workflow.signal
    def approval_decided(self, decision: dict[str, Any]) -> None:
        self._decision = decision

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"status": self._status, "payload": self._payload}

    @workflow.run
    async def run(self, task: str) -> RunResult:
        thread_id = f"{workflow.info().workflow_id}:agent:0"
        results: list[AgentNodeResult] = []
        resume: dict[str, Any] | None = None
        while True:
            res = await workflow.execute_activity(
                run_agent_node,
                AgentNodeInput(thread_id, task, resume),
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5),
            )
            results.append(res)
            if res.status != "interrupted":
                self._status = "succeeded"
                return RunResult(res.answer, results)
            # HITL: no worker slot is held while waiting; the wait survives worker restarts.
            self._status, self._payload = "waiting_approval", res.payload
            await workflow.wait_condition(lambda: self._decision is not None)
            resume, self._decision = self._decision, None
            self._status, self._payload = "running", None
