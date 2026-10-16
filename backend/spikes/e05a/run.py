"""E0.5a scenario runner: crash during a tool loop, HITL across a restart, idempotent tools.

    FORGE_SPIKE_DSN=postgresql://... python -m spikes.e05a.run

Exit code 0 only if every check passes. Results are printed as a table and as JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from typing import Any

from temporalio.client import Client, WorkflowHandle

from spikes.e05a import store
from spikes.e05a.temporal_defs import TASK_QUEUE, AgentRunWorkflow, RunResult

TIMEOUT_S = 120


def start_worker() -> subprocess.Popen[bytes]:
    proc = subprocess.Popen([sys.executable, "-m", "spikes.e05a.worker"], env=os.environ.copy())
    print(f"  worker started pid={proc.pid}")
    return proc


def kill(proc: subprocess.Popen[bytes]) -> None:
    proc.send_signal(signal.SIGKILL)  # no graceful shutdown: simulates a node crash
    proc.wait()
    print(f"  worker pid={proc.pid} killed with SIGKILL")


async def wait_for(predicate: Any, what: str) -> float:
    start = time.monotonic()
    while time.monotonic() - start < TIMEOUT_S:
        if await predicate():
            return time.monotonic() - start
        await asyncio.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {what}")


async def main() -> int:
    await store.setup()
    client = await Client.connect(os.environ.get("FORGE_TEMPORAL_ADDRESS", "127.0.0.1:7233"))
    wf_id = f"spike-e05a-{uuid.uuid4().hex[:8]}"
    thread = f"{wf_id}:agent:0"
    timings: dict[str, float] = {}

    print(f"run {wf_id}")
    worker = start_worker()
    handle: WorkflowHandle[AgentRunWorkflow, RunResult] = await client.start_workflow(
        AgentRunWorkflow.run, "quarterly numbers", id=wf_id, task_queue=TASK_QUEUE
    )

    async def lookup_done() -> bool:
        return any(e["kind"] == "tool_effect" for e in await store.events(thread))

    print("1. crash after the lookup side effect, during post-processing")
    await wait_for(lookup_done, "lookup side effect")
    kill(worker)
    crash_at = time.monotonic()
    worker = start_worker()

    async def waiting_approval() -> bool:
        return (await handle.query(AgentRunWorkflow.status))["status"] == "waiting_approval"

    await wait_for(waiting_approval, "waiting_approval")
    timings["crash_to_waiting_approval_s"] = round(time.monotonic() - crash_at, 1)
    payload = (await handle.query(AgentRunWorkflow.status))["payload"]
    print(f"2. run is waiting for approval: {payload}")

    print("3. restart the worker while the run waits for approval")
    kill(worker)
    await asyncio.sleep(2)
    worker = start_worker()

    print("4. approve with an edited subject")
    signal_at = time.monotonic()
    await handle.signal(
        AgentRunWorkflow.approval_decided, {"action": "approve", "subject": "Q3 report"}
    )
    result = await asyncio.wait_for(handle.result(), TIMEOUT_S)
    timings["signal_to_completion_s"] = round(time.monotonic() - signal_at, 1)
    kill(worker)

    ev = await store.events(thread)
    counts = Counter(f"{e['kind']}:{e['name']}" for e in ev)
    llm_pids = {e["pid"] for e in ev if e["kind"] == "llm_step"}
    checks = {
        "run succeeded with the expected answer": result.answer
        == "lookup(quarterly numbers)=42 | email sent: Q3 report",
        "activity retried after the crash (attempt 2 seen)": counts["activity_attempt:2"] >= 1,
        "retry continued from checkpoint, not from scratch": counts["invoke:continue"] == 1
        and counts["invoke:fresh"] == 1,
        "LLM step 1 not re-executed after the crash": counts["llm_step:1"] == 1,
        "lookup node re-ran after the crash": counts["tool_node:lookup"] == 2,
        "lookup side effect happened exactly once (idempotency)": counts["tool_effect:lookup"] == 1,
        "approval resumed with Command(resume)": counts["invoke:resume"] == 1,
        "email sent exactly once, with the edited subject": counts[
            "tool_effect:send_email:Q3 report"
        ]
        == 1
        and sum(1 for k in counts if k.startswith("tool_effect:send_email")) == 1,
        "work spread over 3 worker processes": len({e["pid"] for e in ev}) >= 3
        and len(llm_pids) >= 2,
    }

    print("\nchecks")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    report = {
        "workflow_id": wf_id,
        "timings": timings,
        "event_counts": dict(sorted(counts.items())),
        "activity_results": [r.__dict__ for r in result.activity_results],
        "checks": checks,
    }
    print("\n" + json.dumps(report, indent=2, default=str))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
