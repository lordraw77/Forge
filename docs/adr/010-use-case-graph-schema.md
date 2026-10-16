# ADR-010 – Use case graph JSON schema v1

- Status: Accepted
- Date: 2026-10-16

## Context

Node types are described three ways (F5.3, use case JSON, Part 2 Graphical pattern), and the spec has both `workflow` and `use_case` entities.

## Decision

- **One concept**: use case (`use_case`, `use_case_version`). No `workflow` tables. F5 "workflow" = use case with `pattern: "graph"`.
- **Canonical node types**: `start`, `agent`, `tool`, `llm`, `router`, `for_each`, `while`, `human_approval`, `end`; `code` added in Phase 3 (sandbox).

  | Spec term | Canonical |
  | --- | --- |
  | Agentic | `agent` |
  | condition | `router` |
  | loop / For Loop / While Loop | `for_each` / `while` |
  | parallel | implicit: nodes whose dependencies complete together run in parallel |
  | human approval | `human_approval`, or `hitl: true` on `agent`/`tool` |

- **Dependencies**: `after: ["node", "router.section", "loop.loop", "loop.done"]`, `after_mode: "all" | "any"` (default `all`).
- **Router**: ordered sections, each with conditions (`field`, `op` ∈ `= != > < contains`, `value`) combined `and`/`or`; several sections may match (fan-out); `default` path when none match.
- **Dead-path elimination**: nodes on branches not taken are marked `skipped`. A node with `after_mode: all` runs when all parents are completed or skipped and at least one is completed; if all are skipped, it is skipped too.
- **Loops**: `for_each.items` (variable or static list), `while.condition`; both require `max_iterations` (default 50, hard cap from project settings). Iteration index is part of step identity.
- **Variables**: `{name}` references; `{item}` and `{index}` inside `for_each`; resolved from a run-scoped variable map.
- **References** pinned: `agent_ref: "name@version"`, same for tools and prompts.
- **Validation on save**: JSON Schema (published at `/api/v1/schemas/use-case-graph/v1`) + semantic checks: unique ids, no unreachable nodes, no unbounded cycles, every variable defined on all paths before use, referenced versions exist and are published.
- Execution by the Forge graph engine as Temporal workflow code (ADR-004).

## Consequences

- The visual editor, the Planner Agent and import/export all target one schema; the Planner uses it as its structured output schema.
- `schema_version` field allows later evolution.
- Canvas layout is stored in a non-semantic `ui.positions` block (node id → x/y); validation, versioning diffs and execution ignore it. Output connector ids in the editor are exactly the `after` references (spike E0.5c).
