# ADR-004 – Temporal for durability, LangGraph for agent reasoning

- Status: Accepted (PO chose to keep both, 2026-10-16)
- Date: 2026-10-16

## Context

The spec lists LangGraph (graphs, PostgreSQL checkpoints, HITL) and Temporal (durable jobs). Both can persist execution state, so each layer needs a clear job, or state ends up stored twice with conflicting recovery behaviour.

Requirements: runs resume after a worker crash from the last completed step (AC6); HITL waits can last days (AC5); ingestion and re-sync are scheduled; agents support ReAct, plan-and-execute, router and supervisor patterns.

## Decision

Two layers with one owner each.

```text
Temporal workflow (outer, coarse-grained)       LangGraph (inner, fine-grained)
-----------------------------------------       --------------------------------
UseCaseRunWorkflow                               compiled per agent version
  Forge graph engine (ADR-010):                    ReAct / plan-and-execute /
  start → agent → router → for_each → ...          router / supervisor graphs
  each node = one activity  ───────────────►     runs inside the activity
  HITL = wait for signal (days)                    checkpoints in PostgreSQL
  limits, cancellation, timers                     interrupt() for approval
```

**Temporal owns**

- Run lifecycle and state (`queued → running → waiting_approval → … → succeeded | failed | cancelled`).
- The use case graph (our JSON, ADR-010), executed by deterministic workflow code; every node is an activity.
- Long waits: HITL approvals are Temporal **signals**; reminders and timeouts are timers.
- Schedules: RAG re-sync, cron triggers, purge jobs.
- Ingestion workflows (parse → chunk → embed → index, per document activities).
- Run-level limits: `max_steps`, `timeout_s`, `max_cost_usd`, cancellation.

**LangGraph owns**

- What happens *inside* one agent node: the reasoning loop, tool calls, sub-agent delegation for the supervisor pattern.
- Fine-grained recovery inside that loop through `AsyncPostgresSaver` checkpoints.

**How they connect**

1. Activity `run_agent_node(run_id, node_id, iteration, input)` builds the agent's LangGraph from the pinned `agent_version` and runs it with `thread_id = "{run_id}:{node_id}:{iteration}"`.
2. If the worker crashes, Temporal retries the activity; the same `thread_id` makes LangGraph continue from its last checkpoint instead of starting over.
3. A tool with `requires_approval`, or an agent with Human Input Mode, calls LangGraph `interrupt(payload)`. The activity ends with result `interrupted(payload)`.
4. The workflow records an `approval`, sets the run to `waiting_approval`, notifies, and waits for the `approval_decided` signal.
5. On the signal, the workflow schedules `run_agent_node` again with `resume=decision`; the activity calls the graph with `Command(resume=decision)` on the same `thread_id`.
6. Node-level HITL (`human_approval` node, `hitl: true`) does not involve LangGraph: it is a signal wait in the workflow.

**Rules**

- **LLM calls from LangGraph go through the Forge gateway** (an in-process `BaseChatModel` adapter over `gateway`), never directly to provider SDKs, so quotas, guardrails, routing, cost and tracing always apply.
- **Tool calls are idempotent.** LangGraph re-runs the interrupted node from its start on resume, and Temporal retries activities. Each tool invocation carries an idempotency key `{thread_id}:{tool_call_id}`; side-effecting tools record results in `tool_call_log` and return the stored result on repeat.
- Streaming to the UI: the activity publishes token and step events to Redis (`run:{run_id}` channel); the API relays them over SSE. Activity heartbeats carry progress.
- Checkpoint tables live in schema `langgraph`, accessible to `forge_app` only from workers. They have no `project_id`, so access is only by `thread_id` derived from a run already authorised under RLS. Checkpoints are deleted when the run is terminal + 7 days, and on project purge (by `run_id` prefix).
- Activity timeouts: `start_to_close` per node = node timeout; heartbeat timeout 60 s; retries with backoff, non-retryable on validation, guardrail block and budget exceeded.
- Temporal persistence: PostgreSQL, separate database `temporal` on the same cluster.

## Consequences

- AC6 is met at two levels: node (Temporal) and agent step (LangGraph).
- Days-long HITL waits do not hold a worker slot or a LangGraph process.
- Two dependencies to operate and upgrade; mitigated by limiting LangGraph to the `agents` module behind an `AgentRuntime` interface.
- Phase 0 spike E0.5a must prove: kill a worker during a tool loop → resume at the last LangGraph checkpoint; interrupt → signal after a restart → resume.

## Alternatives

- Temporal only, with a hand-written agent loop: simpler, but rejected by the PO in favour of LangGraph's agent patterns.
- LangGraph only: no schedules, weaker multi-day waits and worker fleet management.
