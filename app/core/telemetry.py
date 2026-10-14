"""OpenTelemetry tracing: provider, OTLP export, and auto-instrumentation.

Spans are exported with a ``BatchSpanProcessor``, so export happens on a
background thread and never on the request path. With no OTLP endpoint the
provider still runs: trace ids exist, logs carry them, responses return them,
and nothing is shipped.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings

logger = structlog.get_logger(__name__)

_provider: TracerProvider | None = None

tracer = trace.get_tracer("app")


def configure_tracing(settings: Settings) -> TracerProvider | None:
    """Install the global tracer provider once. Safe to call repeatedly."""
    global _provider
    if not settings.otel_enabled:
        logger.info("tracing disabled")
        return None
    if _provider is not None:
        return _provider

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.service_version,
            "deployment.environment": settings.app_env,
        }
    )
    provider = TracerProvider(resource=resource)
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("otlp trace export configured", endpoint=endpoint)
    else:
        logger.info("otlp endpoint unset; spans are not exported")

    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    _provider = provider
    return provider


def instrument_app(app: FastAPI, settings: Settings) -> None:
    """Server span per request; honours an incoming W3C ``traceparent``."""
    if not settings.otel_enabled:
        return
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=settings.otel_excluded_urls,
        tracer_provider=trace.get_tracer_provider(),
    )


def instrument_engine(engine: AsyncEngine, settings: Settings) -> None:
    """One span per SQL statement, children of whatever span issued it."""
    if not settings.otel_enabled:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine, tracer_provider=trace.get_tracer_provider()
    )


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def flush_tracing() -> None:
    """Flush buffered spans when the app stops. Best-effort; never raises.

    Only a flush: the provider is process-global and shuts itself down at exit
    (``shutdown_on_exit``), so an app restarted in-process keeps tracing.
    """
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=5000)
    except Exception:
        logger.warning("tracer provider flush failed", exc_info=True)
