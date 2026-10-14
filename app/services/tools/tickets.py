"""Business action: open a support ticket."""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedUser
from app.core.context import get_request_id
from app.core.metrics import TICKETS_CREATED
from app.core.telemetry import current_trace_id
from app.repositories.tickets import TicketRepository
from app.schemas.tickets import Priority, TicketOut

logger = structlog.get_logger(__name__)

_PRIORITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})


async def create_ticket(
    session: AsyncSession,
    user: AuthenticatedUser,
    *,
    title: str,
    priority: str,
    description: str = "",
) -> TicketOut:
    # The model chose these values; normalise rather than trust them.
    normalised: Priority = priority.lower() if priority.lower() in _PRIORITIES else "medium"  # type: ignore[assignment]
    ticket = await TicketRepository(session).create(
        user_id=user.id,
        title=title.strip()[:200] or "Untitled ticket",
        description=description.strip(),
        priority=normalised,
        request_id=get_request_id(),
        trace_id=current_trace_id(),
    )
    TICKETS_CREATED.labels(priority=normalised).inc()
    logger.info("ticket created", ticket_id=ticket.id, priority=normalised)
    return TicketOut.model_validate(ticket)
