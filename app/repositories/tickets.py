from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        title: str,
        description: str,
        priority: str,
        request_id: str | None,
        trace_id: str | None,
    ) -> Ticket:
        ticket = Ticket(
            user_id=user_id,
            title=title,
            description=description,
            priority=priority,
            request_id=request_id,
            trace_id=trace_id,
        )
        self._session.add(ticket)
        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def get_for_user(self, ticket_id: int, user_id: uuid.UUID) -> Ticket | None:
        ticket = await self._session.get(Ticket, ticket_id)
        # Another user's ticket reads as missing, not forbidden: ids are not a secret oracle.
        if ticket is None or ticket.user_id != user_id:
            return None
        return ticket
