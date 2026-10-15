"""Public configuration for the web console at /ui (no secrets)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import AppSettings

router = APIRouter(tags=["console"])


class ConsoleLinks(BaseModel):
    swagger: str = "/docs"
    grafana: str
    jaeger: str
    prometheus: str
    alertmanager: str
    kafka_ui: str
    langfuse: str | None = Field(description="Langfuse host, when Langfuse is configured.")


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
            langfuse=settings.langfuse_host if settings.langfuse_enabled else None,
        ),
    )


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
