"""Spike worker process: `python -m spikes.e05a.worker` (killed and restarted by the runner)."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from temporalio.client import Client
from temporalio.worker import Worker

from spikes.e05a import store, temporal_defs
from spikes.e05a.temporal_defs import TASK_QUEUE, AgentRunWorkflow, run_agent_node


async def main() -> None:
    pool = store.checkpoint_pool()
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await checkpointer.setup()
    temporal_defs.CHECKPOINTER = checkpointer
    client = await Client.connect(os.environ.get("FORGE_TEMPORAL_ADDRESS", "127.0.0.1:7233"))
    print(f"spike worker pid={os.getpid()} polling {TASK_QUEUE}", flush=True)
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AgentRunWorkflow],
        activities=[run_agent_node],
        # After a crash, workflow tasks routed to the dead worker's sticky queue are re-routed
        # only after this timeout (SDK default 10 s). See docs/spikes/e05a-temporal-langgraph.md.
        sticky_queue_schedule_to_start_timeout=timedelta(
            seconds=float(os.environ.get("SPIKE_STICKY_TIMEOUT_S", "3"))
        ),
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
