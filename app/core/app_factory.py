"""Build the FastAPI app: logging, tracing, middleware, handlers, routers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI

from app.api.openapi import document_common_headers
from app.api.routes import ask, health, metrics, models, tickets
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.metrics import ASK_ERRORS
from app.core.telemetry import (
    configure_tracing,
    flush_tracing,
    instrument_app,
    instrument_engine,
)
from app.db.session import Database
from app.exceptions.codes import ErrorCode
from app.exceptions.handlers import register_error_hook, register_exception_handlers
from app.llm.factory import build_http_client, build_providers
from app.llm.langfuse_recorder import LangfuseRecorder
from app.llm.orchestrator import LLMOrchestrator
from app.llm.registry import sync_catalog
from app.middleware.metrics import MetricsMiddleware
from app.middleware.request_context import RequestContextMiddleware

logger = structlog.get_logger(__name__)


def _count_error(code: ErrorCode) -> None:
    ASK_ERRORS.labels(code=code.value).inc()


register_error_hook(_count_error)


async def _sync_catalog(db: Database) -> None:
    """Keep the built-in catalog current on boot. A failure (no schema yet) is
    logged, not fatal: readiness reports the database, and `make seed` exists."""
    try:
        async with db.sessionmaker() as session:
            changed = await sync_catalog(session)
        logger.info("model catalog synced", changed=changed)
    except Exception as exc:
        logger.warning("model catalog sync skipped", error=type(exc).__name__)


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(level=settings.log_level, log_format=settings.log_format)
    configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = database or Database.from_settings(settings)
        instrument_engine(db.engine, settings)
        app.state.db = db
        if settings.auto_sync_catalog:
            await _sync_catalog(db)

        http_client = build_http_client(settings)
        recorder = LangfuseRecorder.from_settings(settings)
        app.state.orchestrator = LLMOrchestrator(
            build_providers(http_client, settings), settings, recorder
        )
        logger.info("service started", env=settings.app_env, version=settings.service_version)
        try:
            yield
        finally:
            await http_client.aclose()
            if recorder is not None:
                recorder.shutdown()
            await db.dispose()
            flush_tracing()
            logger.info("service stopped")

    app = FastAPI(
        title="Observable AI Service",
        version=settings.service_version,
        description=(
            "AI-backed `/ask` API with a multi-provider LLM orchestrator, "
            "structured logs, Prometheus metrics and OpenTelemetry traces.\n\n"
            "**Auth:** click **Authorize** and paste your `X-API-Key` (from `make seed`). "
            "It is sent on every request; `/health`, `/health/ready` and `/metrics` "
            "need no key.\n\n"
            "**Tracing:** every response has an `X-Request-ID` header, and `/ask` returns "
            "a `trace_id` to open in Jaeger at `http://localhost:16686/trace/<trace_id>`.\n\n"
            '**Errors:** always `{"error": {code, message, request_id, details?}}`.'
        ),
        lifespan=lifespan,
        # Documents X-Request-ID / traceparent on every route; the middleware does the work.
        dependencies=[Depends(document_common_headers)],
        # Keep the key entered under "Authorize" across page reloads.
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.state.settings = settings

    # Last added runs first: request context wraps metrics, so the access log
    # and the metrics agree on the status of the same request.
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(ask.router)
    app.include_router(models.router)
    app.include_router(tickets.router)

    # Outermost: the server span exists before our middleware logs anything.
    instrument_app(app, settings)
    return app
