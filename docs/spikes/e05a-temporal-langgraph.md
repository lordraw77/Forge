# Spike E0.5a – Temporal + LangGraph (ADR-004)

_Date: 2026-10-16. Code: [backend/spikes/e05a/](../../backend/spikes/e05a/). Run: `make spike/e05a`._

## Question

Can Temporal (outer durability) and LangGraph (agent loop inside one activity) work together as ADR-004 describes? Specifically:

1. A worker crash in the middle of an agent's tool loop resumes from the last LangGraph checkpoint, not from scratch.
2. An approval (`interrupt()`) becomes a Temporal wait that holds no worker and survives worker restarts.
3. Side-effecting tools run exactly once despite node re-execution and activity retries.

**Result: yes, all three hold.** 9 of 9 checks passed in 6 of 6 runs.

## Setup

- The agent is a LangGraph `StateGraph` with two nodes, `planner` and `tools`, and an `AsyncPostgresSaver` checkpointer that stores its tables in the `langgraph` schema.
- The "LLM" is scripted, so the test is deterministic and needs no model endpoint:
  1. call `lookup`;
  2. call `send_email`, which requires approval;
  3. answer.
- The Temporal workflow `AgentRunWorkflow` runs the agent in the activity `run_agent_node`. On `interrupted` it sets its status to `waiting_approval` and waits for the signal `approval_decided`, then runs the activity again with `resume`.
- Every node execution is written to a journal table. That makes re-executions countable.
- Infrastructure is the Compose stack: PostgreSQL 17 (ParadeDB) and Temporal 1.29.7. The worker runs on the host with LangGraph 1.2.15, langgraph-checkpoint-postgres 3.2.0 and temporalio 1.34.0.

## Scenario

| Step | Action |
| --- | --- |
| 1 | Start the run. Once the `lookup` side effect has happened, while the tool is still post-processing for 6 s, **SIGKILL** the worker. Start a new one. |
| 2 | Wait until the run reports `waiting_approval` with the email payload. |
| 3 | **SIGKILL** the worker while the run is waiting. Start a new one. |
| 4 | Send `approval_decided` with an edited subject (`Q3 report`). Wait for the result. |

## Results

| Check | Evidence (event counts of one run) | Result |
| --- | --- | --- |
| Run succeeded with the expected answer | `lookup(quarterly numbers)=42 \| email sent: Q3 report` | PASS |
| Activity retried after the crash | `activity_attempt:2 = 1` | PASS |
| Retry continued from the checkpoint | `invoke:fresh = 1`, `invoke:continue = 1` | PASS |
| LLM step 1 not re-executed | `llm_step:1 = 1` | PASS |
| Interrupted node re-ran | `tool_node:lookup = 2` | PASS |
| Lookup side effect exactly once | `tool_effect:lookup = 1` | PASS |
| Approval resumed with `Command(resume)` | `invoke:resume = 1` | PASS |
| Email sent once, with the edited subject | `tool_effect:send_email:Q3 report = 1` | PASS |
| Work spread over 3 worker processes | 3 distinct PIDs in the journal | PASS |

| Timing (median of runs) | SDK defaults | After tuning |
| --- | --- | --- |
| Crash → run waiting for approval | 17.8 s | 17.7 s |
| Approval signal → run completed, after a worker crash | 10.7 s | 3.7 s |

The crash-recovery time of about 18 s breaks down as follows:

- about 10 s of `heartbeat_timeout`, the time Temporal needs to detect the dead worker;
- about 2 s for the new worker to start;
- 6 s of tool post-processing, which is re-executed.

## Findings and rules for the implementation

1. **Choose the start mode from the checkpoint, never from the activity input.** A retried activity must not re-submit the initial input, because LangGraph would then start a second run on the same thread. `run_agent` reads `aget_state()` and decides:

   | Checkpoint state | Invocation |
   | --- | --- |
   | No checkpoint | Initial input (`fresh`) |
   | Pending next nodes | `None`, continuing from the checkpoint (`continue`) |
   | A `resume` value was passed | `Command(resume=…)` (`resume`) |
   | Finished | Return the stored answer (`finished`) |

2. **An interrupted node re-runs from its start.** This happens both after a crash and after `interrupt()`. Code before `interrupt()` must be free of side effects, and every side-effecting tool needs an idempotency key `{thread_id}:{tool_call_id}` backed by `tool_call_log`. The spike proved the key is what prevents the duplicate: the `lookup` node ran twice, its effect once.
3. **There is still an at-least-once window.** If the worker dies after the external effect but before `tool_call_log` is written, the effect repeats. Connectors that support it must forward the idempotency key to the remote system, for example as an `Idempotency-Key` header or a ticket external ID.
4. **Heartbeats.** A background task heartbeats every 2 s for the whole activity, because LangGraph nodes do not heartbeat themselves. `heartbeat_timeout` sets the crash-detection time: 10 s in the spike. It should be set per node type and kept short for interactive runs.
5. **Sticky queue timeout.** After a crash, a workflow task routed to the dead worker's sticky queue waits for `sticky_queue_schedule_to_start_timeout`, which is 10 s by default. Setting it to 3 s cut signal-to-completion from 10.7 s to 3.7 s. Graceful shutdown (SIGTERM) avoids the delay entirely.
6. **The workflow sandbox stays clean.** LangGraph and psycopg are imported only inside the activity. Workflow code imports only `temporalio` and dataclasses.
7. **Checkpointer.** Use one `AsyncConnectionPool` per worker process with `autocommit=True`, `row_factory=dict_row`, `prepare_threshold=0` and `search_path=langgraph`. The spike ran `setup()` as the database owner. In the product, the checkpoint DDL must be applied by an Alembic migration, because `forge_app` has no DDL rights, and `forge_app` gets DML grants on the `langgraph` schema only.
8. **The HITL wait holds nothing.** During `waiting_approval` no activity is running. Killing the worker had no effect, and the new worker replayed the workflow and resumed it on the signal.

## Not covered (follow-ups)

| Gap | Where it gets covered |
| --- | --- |
| Real LLM through the gateway adapter (`BaseChatModel` over `forge.gateway`) | E1.18 |
| Token streaming to the UI via Redis and SSE during an activity | E1.19 |
| Concurrency (NFR: 200 concurrent runs per worker node) and pool sizing | E3.7 load tests; quick check in E1.18 |
| Running as `forge_app` with grants on the `langgraph` schema; checkpoint retention (terminal + 7 days) | E1.18 migration |
| Multi-node use-case graphs (`for_each`, router) on top of this activity | E2.6 |

## Decision

ADR-004 is **confirmed**. The rules above are added to it.
