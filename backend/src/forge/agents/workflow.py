"""Temporal side of an agent run (ADR-004): the run's lifecycle. The agent loop itself runs in
the activity `run_agent`; this module never imports LangGraph or psycopg (rule 5)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

RUN_ACTIVITY = "run_agent"
FAIL_ACTIVITY = "fail_run"
# Crash detection time (spike E0.5a): a dead worker is noticed after this long without a beat.
HEARTBEAT_TIMEOUT = timedelta(seconds=15)


@workflow.defn(name="AgentRunWorkflow")
class AgentRunWorkflow:
    """One chat turn. Retries after a worker crash continue from the last checkpoint; when
    every attempt is used up the run is failed explicitly, so it never stays `running`."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = int(payload.get("timeout_s", 120))
        try:
            result: dict[str, Any] = await workflow.execute_activity(
                RUN_ACTIVITY,
                payload,
                start_to_close_timeout=timedelta(seconds=timeout + 30),
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RetryPolicy(
                    maximum_attempts=4,
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    non_retryable_error_types=["NonRetryable"],
                ),
            )
            return result
        except ActivityError as exc:
            reason = str(exc.cause or exc)[:300]
            await workflow.execute_activity(
                FAIL_ACTIVITY,
                {**payload, "code": "worker_failed", "message": reason},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            return {"status": "failed", "error_code": "worker_failed"}
