"""Prometheus metrics (ADR-009, US-1.20.2). Labels never carry user, prompt or document
content: only names of models, tools and routes, and outcomes."""

from __future__ import annotations

from decimal import Decimal

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from forge.core.logging import get_logger

log = get_logger(__name__)

_LATENCY = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

HTTP_REQUESTS = Counter("forge_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "forge_http_request_duration_seconds", "HTTP request duration", ["method", "route"],
    buckets=_LATENCY,
)  # fmt: skip
GATEWAY_CALLS = Counter(
    "forge_gateway_calls_total", "Gateway model calls", ["endpoint", "model", "status"]
)
GATEWAY_TOKENS = Counter(
    "forge_gateway_tokens_total", "Tokens through the gateway", ["model", "direction"]
)
GATEWAY_COST = Counter("forge_gateway_cost_usd_total", "Gateway cost in USD", ["model"])
GATEWAY_LATENCY = Histogram(
    "forge_gateway_call_duration_seconds", "Gateway call duration", ["endpoint", "model"],
    buckets=_LATENCY,
)  # fmt: skip
AGENT_RUNS = Counter("forge_agent_runs_total", "Agent runs by final status", ["status"])
AGENT_RUN_DURATION = Histogram(
    "forge_agent_run_duration_seconds", "Agent run duration", buckets=_LATENCY
)
AGENT_STEPS = Counter("forge_agent_steps_total", "Agent steps", ["kind"])
TOOL_CALLS = Counter("forge_tool_calls_total", "Agent tool calls", ["tool", "status"])
ACTIVITIES_INFLIGHT = Gauge(
    "forge_worker_activities_inflight", "Temporal activities running in this worker", ["activity"]
)


_started = False


def start_server(port: int) -> bool:
    """Serve `/metrics` on an internal port, separate from the public API (0 = off)."""
    global _started
    if port <= 0 or _started:
        return False
    try:
        start_http_server(port)
    except OSError as exc:  # port taken: the process still works, say so
        log.warning("metrics.server_failed", port=port, error=str(exc))
        return False
    _started = True
    log.info("metrics.server_started", port=port)
    return True


def gateway_call(
    endpoint: str,
    model: str | None,
    status: str,
    tokens_in: int,
    tokens_out: int,
    cost: Decimal,
    latency_ms: int,
) -> None:
    name = model or "none"
    GATEWAY_CALLS.labels(endpoint, name, status).inc()
    GATEWAY_LATENCY.labels(endpoint, name).observe(latency_ms / 1000)
    if tokens_in:
        GATEWAY_TOKENS.labels(name, "in").inc(tokens_in)
    if tokens_out:
        GATEWAY_TOKENS.labels(name, "out").inc(tokens_out)
    if cost:
        GATEWAY_COST.labels(name).inc(float(cost))
