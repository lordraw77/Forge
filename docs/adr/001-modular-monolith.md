# ADR-001 – Modular monolith with Temporal workers

- Status: Accepted
- Date: 2026-10-16

## Context

The spec shows several logical services (gateway, prompt, RAG, tools, agent runtime, guardrails) and requires stateless, horizontally scalable processes. The team is 3–4 developers. Microservices would add network contracts, distributed transactions and deployment overhead before there is any scaling need.

## Decision

- One Python codebase (`backend/src/forge/`), split into modules with explicit public interfaces (`iam`, `tenancy`, `admin`, `gateway`, `prompts`, `rag`, `tools`, `agents`, `usecases`, `guardrails`, `observability`, `mcp_server`, plus `core`).
- Modules call each other only through their `service` interface, never through another module's repositories or tables. Enforced in CI with `import-linter` contracts.
- The same image runs in several roles, chosen by command:

  | Process | Command | Content |
  | --- | --- | --- |
  | `forge-api` | `forge api` | FastAPI app |
  | `forge-worker-runs` | `forge worker runs` | Temporal worker, task queue `runs` |
  | `forge-worker-ingest` | `forge worker ingest` | Temporal worker, task queue `ingest` |
  | `forge-sandbox` | separate image (Phase 3) | gVisor tool runner |

- All processes are stateless; state lives in PostgreSQL, Redis, MinIO and Temporal.

## Consequences

- One build, one version, simple local development.
- A module can be extracted into a service later because its boundary is already an interface.
- Heavy dependencies (Docling, Presidio) end up in the shared image; mitigated with an optional `ingest` extra and a separate image tag if image size becomes a problem.

## Alternatives

- Microservices per logical service: rejected for team size and MVP timeline.
