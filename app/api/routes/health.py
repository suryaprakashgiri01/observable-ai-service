from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import AppSettings, Db

router = APIRouter(prefix="/health", tags=["observability"])
logger = structlog.get_logger(__name__)


@router.get("", summary="Liveness")
async def liveness(settings: AppSettings) -> dict[str, str]:
    """The process is up and serving. Checks nothing else, by design: a liveness
    probe that checks the database restarts healthy pods during a DB outage."""
    return {"status": "ok", "version": settings.service_version}


@router.get(
    "/ready",
    summary="Readiness",
    responses={
        503: {
            "description": "Database unreachable",
            "content": {
                "application/json": {
                    "example": {
                        "status": "not_ready",
                        "checks": {"database": "unavailable"},
                        "providers": {
                            "openai": False,
                            "anthropic": False,
                            "google": False,
                            "mock": True,
                        },
                    }
                }
            },
        }
    },
)
async def readiness(request: Request, db: Db, settings: AppSettings) -> JSONResponse:
    """Ready to take traffic: database reachable. Reports which LLM providers have
    credentials; none is fatal because `mock-echo` always answers."""
    checks: dict[str, str] = {}
    try:
        await db.ping()
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("readiness check failed", check="database", error=type(exc).__name__)
        checks["database"] = "unavailable"

    providers = {
        "openai": bool(settings.openai_api_key.get_secret_value()),
        "anthropic": bool(settings.anthropic_api_key.get_secret_value()),
        "google": bool(settings.gemini_api_key.get_secret_value()),
        "mock": True,
    }
    events = getattr(request.app.state, "events", None)
    if events is not None and events.enabled:
        # Reported, not gating: Kafka is optional fan-out, the API works without it.
        checks["kafka"] = events.status()
    ready = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": checks,
            "providers": providers,
        },
    )
