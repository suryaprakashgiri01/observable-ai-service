from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertStatus = Literal["firing", "resolved"]


class AlertmanagerAlert(BaseModel):
    """One alert of an Alertmanager v4 webhook payload (unknown fields ignored)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: AlertStatus
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    fingerprint: str = Field(max_length=64)
    generator_url: str | None = Field(default=None, alias="generatorURL")


class AlertmanagerPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    version: str = "4"
    status: AlertStatus
    receiver: str = ""
    alerts: list[AlertmanagerAlert] = Field(max_length=500)


class WebhookAck(BaseModel):
    received: int = Field(description="Alerts stored or updated from this payload.")


class NotificationOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
                "alertname": "Http5xxErrors",
                "severity": "critical",
                "status": "firing",
                "summary": "5xx responses on POST /ask",
                "description": "1 response(s) with status 502 in the last minute.",
                "labels": {"alertname": "Http5xxErrors", "route": "/ask", "status": "502"},
                "starts_at": "2026-10-15T10:00:00Z",
                "ends_at": None,
                "received_at": "2026-10-15T10:00:20Z",
                "read_at": None,
            }
        },
    )

    id: uuid.UUID
    alertname: str
    severity: str = Field(description="critical | warning")
    status: AlertStatus
    summary: str
    description: str
    labels: dict[str, str]
    starts_at: datetime
    ends_at: datetime | None
    received_at: datetime
    read_at: datetime | None


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread: int = Field(description="Unread notifications across all statuses.")
