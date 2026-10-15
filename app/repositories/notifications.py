from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification
from app.schemas.notifications import AlertmanagerAlert

# Alertmanager sends this "zero" end time for alerts that are still firing.
_OPEN_ENDED = datetime(1, 1, 1, tzinfo=UTC)


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, alert: AlertmanagerAlert) -> Notification:
        """One row per alert occurrence (fingerprint + start): firing, then resolved."""
        row = await self._session.scalar(
            select(Notification).where(
                Notification.fingerprint == alert.fingerprint,
                Notification.starts_at == alert.starts_at,
            )
        )
        ends_at = alert.ends_at if alert.ends_at and alert.ends_at > _OPEN_ENDED else None
        if row is None:
            row = Notification(fingerprint=alert.fingerprint, starts_at=alert.starts_at)
            self._session.add(row)
        elif row.status != alert.status:
            row.read_at = None  # a state change is news again
        row.alertname = alert.labels.get("alertname", "unknown")
        row.severity = alert.labels.get("severity", "none")
        row.status = alert.status
        row.summary = alert.annotations.get("summary", "")
        row.description = alert.annotations.get("description", "")
        row.labels = dict(alert.labels)
        row.ends_at = ends_at if alert.status == "resolved" else None
        row.received_at = datetime.now(UTC)
        return row

    async def list(
        self, *, status: str | None, unread: bool | None, limit: int
    ) -> list[Notification]:
        query = select(Notification).order_by(Notification.received_at.desc()).limit(limit)
        if status is not None:
            query = query.where(Notification.status == status)
        if unread is True:
            query = query.where(Notification.read_at.is_(None))
        elif unread is False:
            query = query.where(Notification.read_at.is_not(None))
        return list((await self._session.scalars(query)).all())

    async def count_unread(self) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
        )
        return int(count or 0)

    async def mark_read(self, notification_id: uuid.UUID) -> Notification | None:
        row = await self._session.get(Notification, notification_id)
        if row is not None and row.read_at is None:
            row.read_at = datetime.now(UTC)
        return row
