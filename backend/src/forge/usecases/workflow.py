"""Temporal side of a use case run (ADR-004, ADR-010). The graph engine runs inside the workflow
(deterministic: no I/O), every node execution is an activity. A worker killed mid-run costs at
most the activity in flight; completed steps are in the workflow history."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.exceptions import CancelledError as TemporalCancelled

with workflow.unsafe.imports_passed_through():
    from forge.usecases.engine import Engine
    from forge.usecases.graph import GraphError

BEGIN_ACTIVITY = "uc_begin_run"
NODE_ACTIVITY = "uc_run_node"
FINISH_ACTIVITY = "uc_finish_run"
APPROVAL_ACTIVITY = "uc_wait_approval"
NOTIFY_ACTIVITY = "uc_notify_run"
NOTIFY_ATTEMPTS = 4
HEARTBEAT_TIMEOUT = timedelta(seconds=15)
NODE_RETRY = RetryPolicy(
    maximum_attempts=4,
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
)
EDGE_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))


APPROVAL_MAX_WAIT = timedelta(hours=721)  # node limit is 720 h; the activity enforces its own


class _Pauses:
    """Time the run spends waiting for people. It does not count against `timeout_s`, which
    limits the work, not the patience of reviewers (an approval has its own limit)."""

    def __init__(self) -> None:
        self.waiting = 0
        self._closed = 0.0
        self._since = 0.0

    def enter(self) -> None:
        if self.waiting == 0:
            self._since = workflow.time()
        self.waiting += 1

    def leave(self) -> None:
        self.waiting -= 1
        if self.waiting == 0:
            self._closed += workflow.time() - self._since

    def total(self) -> float:
        return self._closed + (workflow.time() - self._since if self.waiting else 0.0)


async def _within_budget(task: asyncio.Future[Any], seconds: float, pauses: _Pauses) -> Any:
    """Await `task`, raising TimeoutError after `seconds` of work (waiting for approvals is not
    work) and cancelling it."""
    remaining = seconds
    try:
        while True:
            started, paused = workflow.time(), pauses.total()
            done, _ = await asyncio.wait({task}, timeout=max(remaining, 0.001))
            if done:
                return task.result()
            remaining -= (workflow.time() - started) - (pauses.total() - paused)
            if pauses.waiting:
                await workflow.wait_condition(lambda: pauses.waiting == 0 or task.done())
            elif remaining <= 0.5:
                raise TimeoutError
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


@workflow.defn(name="UseCaseRunWorkflow")
class UseCaseRunWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        prep: dict[str, Any] = await workflow.execute_activity(
            BEGIN_ACTIVITY,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=EDGE_RETRY,
        )
        if prep.get("skipped"):
            return {"status": "skipped"}
        limits = prep["limits"]
        node_timeout = int(limits["timeout_s"]) + 60

        async def execute(call: dict[str, Any]) -> dict[str, Any]:
            try:
                result: dict[str, Any] = await workflow.execute_activity(
                    NODE_ACTIVITY,
                    {**payload, **call},
                    start_to_close_timeout=timedelta(seconds=node_timeout),
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=NODE_RETRY,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, TemporalCancelled):
                    raise asyncio.CancelledError from exc
                raise GraphError(
                    "worker_failed", str(exc.cause or exc)[:300], call["node"]
                ) from exc
            if result.get("error_code"):
                raise GraphError(
                    str(result["error_code"]), str(result.get("error") or ""), call["node"]
                )
            return result

        pauses = _Pauses()

        async def approve(call: dict[str, Any]) -> dict[str, Any]:
            pauses.enter()
            try:
                res: dict[str, Any] = await workflow.execute_activity(
                    APPROVAL_ACTIVITY,
                    {**payload, **call},
                    start_to_close_timeout=APPROVAL_MAX_WAIT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=NODE_RETRY,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, TemporalCancelled):
                    raise asyncio.CancelledError from exc
                raise GraphError(
                    "worker_failed", str(exc.cause or exc)[:300], call["node"]
                ) from exc
            finally:
                pauses.leave()
            if res["decision"] == "expired":
                raise GraphError("approval_timeout", "Nobody decided in time", call["node"])
            if res["decision"] == "cancelled":
                raise GraphError("approval_cancelled", "The approval was cancelled", call["node"])
            return res

        engine = Engine(prep["graph"], prep["inputs"], execute, limits, approve)
        done: dict[str, Any]
        try:
            res = await _within_budget(
                asyncio.ensure_future(engine.run()), float(limits["timeout_s"]), pauses
            )
            done = {
                "status": "succeeded",
                "output": res.output,
                "skipped": res.skipped,
                "decisions": res.decisions,
                "steps": res.steps,
                "cost": res.cost,
                "tokens_in": res.tokens_in,
                "tokens_out": res.tokens_out,
            }
        except GraphError as exc:
            done = {
                "status": "failed",
                "error_code": exc.code,
                "error": exc.message,
                "error_node": exc.node,
                **engine.snapshot(),
            }
        except TimeoutError:
            done = {
                "status": "failed",
                "error_code": "timeout",
                "error": f"The run exceeded {limits['timeout_s']} seconds",
                **engine.snapshot(),
            }
        except asyncio.CancelledError:
            done = {"status": "cancelled", **engine.snapshot()}
        await workflow.execute_activity(
            FINISH_ACTIVITY,
            {**payload, "result": done},
            start_to_close_timeout=timedelta(seconds=120),  # may render the output file
            retry_policy=EDGE_RETRY,
        )
        with contextlib.suppress(ActivityError):  # a dead webhook never changes the run
            await workflow.execute_activity(
                NOTIFY_ACTIVITY,
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=NOTIFY_ATTEMPTS,
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=3.0,
                ),
            )
        return {"status": done["status"]}
