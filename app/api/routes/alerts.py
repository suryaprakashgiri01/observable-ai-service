"""Alertmanager webhook and the in-app notification list."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Body, Header, Path, Query
from opentelemetry import trace

from app.api.deps import AppSettings, Session
from app.api.openapi import AUTH_ERRORS, error_responses
from app.api.services import Events
from app.auth.dependencies import CurrentUser
from app.core.metrics import ALERTS_RECEIVED, NOTIFICATIONS_UNREAD
from app.exceptions import AppError, ErrorCode
from app.repositories.notifications import NotificationRepository
from app.schemas.notifications import (
    AlertmanagerPayload,
    NotificationList,
    NotificationOut,
    WebhookAck,
)

router = APIRouter(tags=["alerts"])
logger = structlog.get_logger(__name__)

_EXAMPLE_PAYLOAD = {
    "version": "4",
    "status": "firing",
    "receiver": "in-app",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "Http5xxErrors",
                "severity": "critical",
                "route": "/ask",
                "status": "502",
            },
            "annotations": {
                "summary": "5xx responses on POST /ask",
                "description": "1 response(s) with status 502 in the last minute.",
            },
            "startsAt": "2026-10-15T10:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": "a1b2c3d4e5f60718",
            "generatorURL": "http://localhost:9090/graph",
        }
    ],
}


@router.post(
    "/alerts/webhook",
    response_model=WebhookAck,
    summary="Alertmanager webhook (receiver)",
    responses=error_responses(ErrorCode.WEBHOOK_UNAUTHORIZED, ErrorCode.VALIDATION_ERROR),
)
async def alert_webhook(
    payload: Annotated[
        AlertmanagerPayload,
        Body(
            openapi_examples={"firing": {"summary": "One firing alert", "value": _EXAMPLE_PAYLOAD}}
        ),
    ],
    session: Session,
    settings: AppSettings,
    events: Events,
    authorization: Annotated[
        str | None,
        Header(
            description="`Bearer <ALERT_WEBHOOK_TOKEN>`; set in deploy/alertmanager.yml.",
            openapi_examples={
                "local": {"summary": "Local default", "value": "Bearer local-alert-token"}
            },
        ),
    ] = None,
) -> WebhookAck:
    """Called by Alertmanager, not by users: authenticated with a shared bearer token
    instead of `X-API-Key`. Each alert becomes, or updates, one in-app notification."""
    expected = f"Bearer {settings.alert_webhook_token.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise AppError(ErrorCode.WEBHOOK_UNAUTHORIZED, "Invalid webhook token")

    repo = NotificationRepository(session)
    span = trace.get_current_span()
    stored = []
    for alert in payload.alerts:
        row = await repo.upsert(alert)
        stored.append(row)
        ALERTS_RECEIVED.labels(alertname=row.alertname, status=row.status).inc()
        log = logger.warning if row.status == "firing" else logger.info
        log(
            f"alert {row.status}",
            alertname=row.alertname,
            severity=row.severity,
            summary=row.summary,
            fingerprint=row.fingerprint,
        )
        span.add_event("alert", {"alertname": row.alertname, "status": row.status})
    await session.commit()
    NOTIFICATIONS_UNREAD.set(await repo.count_unread())
    for row in stored:
        await events.publish(
            "notifications",
            row.fingerprint,
            NotificationOut.model_validate(row).model_dump(mode="json"),
        )
    return WebhookAck(received=len(payload.alerts))


@router.get(
    "/notifications",
    response_model=NotificationList,
    summary="List in-app notifications",
    responses=error_responses(*AUTH_ERRORS),
)
async def list_notifications(
    user: CurrentUser,
    session: Session,
    status: Annotated[
        Literal["firing", "resolved"] | None,
        Query(description="Only `firing` or only `resolved` alerts."),
    ] = None,
    unread: Annotated[
        bool | None, Query(description="`true`: unread only; `false`: read only.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Newest first.")] = 50,
) -> NotificationList:
    """Alerts from Prometheus (5xx, 401/403, fallbacks, latency, failing jobs, service
    down), newest first. Shared by every user: they describe the service, not a user."""
    repo = NotificationRepository(session)
    rows = await repo.list(status=status, unread=unread, limit=limit)
    unread_count = await repo.count_unread()
    NOTIFICATIONS_UNREAD.set(unread_count)
    return NotificationList(
        items=[NotificationOut.model_validate(r) for r in rows], unread=unread_count
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark a notification read",
    responses=error_responses(*AUTH_ERRORS, ErrorCode.NOTIFICATION_NOT_FOUND),
)
async def mark_notification_read(
    notification_id: Annotated[uuid.UUID, Path(description="`id` from `GET /notifications`.")],
    user: CurrentUser,
    session: Session,
) -> NotificationOut:
    repo = NotificationRepository(session)
    row = await repo.mark_read(notification_id)
    if row is None:
        raise AppError(
            ErrorCode.NOTIFICATION_NOT_FOUND, f"Notification {notification_id} not found"
        )
    await session.commit()
    NOTIFICATIONS_UNREAD.set(await repo.count_unread())
    return NotificationOut.model_validate(row)
