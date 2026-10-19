# ADR-009 – OpenTelemetry with traces stored in Forge; Langfuse optional

- Status: Accepted (PO approved Langfuse as optional, 2026-10-16). Amended 2026-10-19: Langfuse is an optional Compose profile, off by default (option C).
- Date: 2026-10-16

## Context

F7 requires per-run tracing, per-call cost and latency, dashboards, Prometheus metrics and JSON logs. Self-hosted Langfuse v3 requires ClickHouse, Redis and S3, which conflicts with a PostgreSQL-only, air-gapped-friendly footprint. Run views and cost totals (AC9) need trace data that Forge can query itself.

## Decision

- All processes instrumented with the OpenTelemetry SDK (FastAPI, SQLAlchemy, httpx, Redis, Temporal interceptors); export via OTLP to an OTel Collector.
- **Forge stores its own run telemetry in PostgreSQL**: `run`, `run_step`, `llm_call`, `tool_call_log`, `guardrail_event`, each with `trace_id`/`span_id`. This is the source for run views, dashboards and cost totals; project totals are computed from these rows, so AC9 holds by construction.
- Collector pipelines (configurable from Admin Console → Observability): Prometheus (metrics), Loki (logs), Tempo or Jaeger (traces), and optional Langfuse / Arize Phoenix as OTLP trace sinks using GenAI semantic conventions.
- Logs: structured JSON (structlog), fields `ts, level, msg, trace_id, span_id, project_id, user_id, run_id`; secrets and PII redacted.
- Metrics prefix `forge_`; RED metrics per endpoint, gateway per model, run states, queue lag, ingestion pages/s.
- Compose ships Collector + Prometheus + Grafana + Loki as an optional profile `observability`.

## Consequences

- Forge works with no external observability stack; Langfuse can be plugged in when wanted.
- Prompt and completion content in traces is configurable (off by default for external sinks).

## Amendment 2026-10-19 (M1.5, PO decision "option C")

The PO reviewed the Langfuse question and chose: **Forge stores its own run records and costs; Langfuse is an optional profile, off by default, fed by OTLP.**

- `deploy/compose/langfuse.yml` (used by `make obs/langfuse`) adds Langfuse web and worker, its own PostgreSQL and ClickHouse, the shared Valkey (database 5) and a `langfuse` bucket on the shared object store, and swaps the collector's configuration for one that also exports traces to Langfuse (`/api/public/otel`, basic auth from generated project keys).
- Forge processes export spans to the collector when `FORGE_OTLP_ENDPOINT=http://otel-collector:4318` is set. Prompt and completion text is not put in spans.
- Nothing in Forge reads from Langfuse: run views, dashboards and AC9 totals come from `agent_run`, `run_step` and `llm_call`. Removing the profile changes nothing in Forge.
- Phase 1 validates the configuration (`docker compose config`) and documents it; a full Langfuse installation is not an exit criterion. Images use the `4` line and are to be pinned when the profile is adopted.
- Metrics: every process serves Prometheus `/metrics` on the internal port `FORGE_METRICS_PORT` (default 9100, never routed through nginx); the `observability` profile scrapes them.
