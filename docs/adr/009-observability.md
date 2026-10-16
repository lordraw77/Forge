# ADR-009 – OpenTelemetry with traces stored in Forge; Langfuse optional

- Status: Accepted (PO approved Langfuse as optional, 2026-10-16)
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
