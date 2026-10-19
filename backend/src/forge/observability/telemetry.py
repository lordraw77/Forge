"""OpenTelemetry setup (ADR-009, US-1.20.2).

A tracer provider is always installed, so every request and run has a trace id for logs and the
run view. Spans are exported over OTLP only when `FORGE_OTLP_ENDPOINT` is set. Spans carry ids,
names and counts, never prompt or completion text.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from forge.core.logging import get_logger
from forge.core.settings import Settings

log = get_logger(__name__)
_configured = False


def setup(service_name: str, settings: Settings) -> None:
    """Install the tracer provider once per process and the library instrumentations."""
    global _configured
    if _configured:
        return
    _configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=_traces_url(settings.otlp_endpoint)))
        )
    trace.set_tracer_provider(provider)

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    log.info("telemetry.started", service=service_name, export=bool(settings.otlp_endpoint))


def _traces_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith("/v1/traces") else f"{base}/v1/traces"


def instrument_app(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/v1/health.*,/healthz,/readyz")


def tracer() -> trace.Tracer:
    return trace.get_tracer("forge")


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None


def current_span_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.span_id:016x}" if ctx.is_valid else None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """A span that records an exception and marks itself failed when the block raises."""
    with tracer().start_as_current_span(name, kind=SpanKind.INTERNAL) as s:
        for key, value in attributes.items():
            if value is not None:
                s.set_attribute(key, value)
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def temporal_interceptors() -> list[Any]:
    """Links API → workflow → activity into one trace (Temporal tracing interceptor)."""
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor()]
