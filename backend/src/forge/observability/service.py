"""Public interface of the observability module (ADR-001, ADR-009)."""

from __future__ import annotations

from forge.observability import metrics
from forge.observability.metrics import gateway_call as record_gateway_call
from forge.observability.metrics import start_server as start_metrics_server
from forge.observability.telemetry import (
    current_span_id,
    current_trace_id,
    instrument_app,
    setup,
    span,
    temporal_interceptors,
)

__all__ = [
    "current_span_id",
    "current_trace_id",
    "instrument_app",
    "metrics",
    "record_gateway_call",
    "setup",
    "span",
    "start_metrics_server",
    "temporal_interceptors",
]
