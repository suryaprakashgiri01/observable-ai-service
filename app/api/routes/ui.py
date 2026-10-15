"""Public configuration for the web console at /ui (no secrets)."""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import AppSettings
from app.core.config import Settings

logger = structlog.get_logger(__name__)

# The Langfuse project id, looked up once (server-side: the secret key never leaves the app).
_langfuse_project: dict[str, str | None] = {}


async def _langfuse_project_url(settings: Settings) -> str | None:
    if not settings.langfuse_enabled:
        return None
    host = settings.langfuse_host.rstrip("/")
    if host not in _langfuse_project:
        try:
            async with httpx.AsyncClient(timeout=3) as http:
                r = await http.get(
                    f"{host}/api/public/projects",
                    auth=(
                        settings.langfuse_public_key,
                        settings.langfuse_secret_key.get_secret_value(),
                    ),
                )
            data = r.json().get("data", []) if r.status_code == 200 else []
            _langfuse_project[host] = f"{host}/project/{data[0]['id']}" if data else None
        except (httpx.HTTPError, ValueError, KeyError):
            logger.warning("langfuse project lookup failed", host=host)
            return None
    return _langfuse_project[host]


router = APIRouter(tags=["console"])


class ConsoleLinks(BaseModel):
    swagger: str = "/docs"
    grafana: str
    jaeger: str
    prometheus: str
    alertmanager: str
    kafka_ui: str
    langfuse: str | None = Field(
        description="Langfuse project URL (append /sessions/<id> or /traces/<id>), when configured."
    )


class ConsoleConfig(BaseModel):
    version: str
    environment: str
    fault_injection: bool = Field(description="X-Debug-Fault is honoured.")
    kafka_enabled: bool
    links: ConsoleLinks


@router.get("/ui/config", response_model=ConsoleConfig, summary="Console configuration")
async def ui_config(settings: AppSettings) -> ConsoleConfig:
    """Flags and public links the console needs. Unauthenticated, and contains no secrets."""
    return ConsoleConfig(
        version=settings.service_version,
        environment=settings.app_env,
        fault_injection=settings.enable_fault_injection,
        kafka_enabled=settings.kafka_enabled,
        links=ConsoleLinks(
            grafana=settings.grafana_url,
            jaeger=settings.jaeger_url,
            prometheus=settings.prometheus_url,
            alertmanager=settings.alertmanager_url,
            kafka_ui=settings.kafka_ui_url,
            langfuse=await _langfuse_project_url(settings),
        ),
    )


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
