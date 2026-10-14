"""Build the FastAPI app: logging, tracing, middleware, handlers, routers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.routes import health, metrics
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.metrics import ASK_ERRORS
from app.core.telemetry import (
    configure_tracing,
    instrument_app,
    instrument_engine,
    shutdown_tracing,
)
from app.db.session import Database
from app.exceptions.codes import ErrorCode
from app.exceptions.handlers import register_error_hook, register_exception_handlers
from app.middleware.metrics import MetricsMiddleware
from app.middleware.request_context import RequestContextMiddleware

logger = structlog.get_logger(__name__)


def _count_error(code: ErrorCode) -> None:
    ASK_ERRORS.labels(code=code.value).inc()


register_error_hook(_count_error)


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(level=settings.log_level, log_format=settings.log_format)
    configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = database or Database.from_settings(settings)
        instrument_engine(db.engine, settings)
        app.state.db = db
        logger.info("service started", env=settings.app_env, version=settings.service_version)
        try:
            yield
        finally:
            await db.dispose()
            shutdown_tracing()
            logger.info("service stopped")

    app = FastAPI(
        title="Observable AI Service",
        version=settings.service_version,
        description=(
            "AI-backed `/ask` API with a multi-provider LLM orchestrator, "
            "structured logs, Prometheus metrics and OpenTelemetry traces."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Last added runs first: request context wraps metrics, so the access log
    # and the metrics agree on the status of the same request.
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(metrics.router)

    # Outermost: the server span exists before our middleware logs anything.
    instrument_app(app, settings)
    return app
