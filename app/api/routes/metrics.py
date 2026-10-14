from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.metrics import REGISTRY

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=True, summary="Prometheus metrics")
async def metrics() -> Response:
    """Prometheus text exposition of every service metric. Unauthenticated, for the scraper."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
