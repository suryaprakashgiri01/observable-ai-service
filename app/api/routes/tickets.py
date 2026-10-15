from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import Session
from app.api.openapi import AUTH_ERRORS, error_responses
from app.auth.dependencies import CurrentUser
from app.exceptions import AppError, ErrorCode
from app.repositories.tickets import TicketRepository
from app.schemas.tickets import TicketList, TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


TicketId = Annotated[
    uuid.UUID,
    Path(
        description="`action.ticket.id` from an `/ask` that created a ticket.",
        openapi_examples={
            "ticket": {"summary": "A ticket id", "value": "3f2a9c1e-6b7d-4e0a-9c51-2d8e7f1b4a60"}
        },
    ),
]


@router.get(
    "",
    response_model=TicketList,
    summary="List my tickets",
    responses=error_responses(*AUTH_ERRORS),
)
async def list_tickets(
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100, description="Newest first.")] = 20,
) -> TicketList:
    """The caller's tickets, newest first. Other users' tickets are never included."""
    rows = await TicketRepository(session).list_for_user(user.id, limit=limit)
    return TicketList(items=[TicketOut.model_validate(r) for r in rows], total=len(rows))


@router.get(
    "/{ticket_id}",
    response_model=TicketOut,
    summary="Get one of my tickets",
    responses=error_responses(*AUTH_ERRORS, ErrorCode.TICKET_NOT_FOUND),
)
async def get_ticket(ticket_id: TicketId, user: CurrentUser, session: Session) -> TicketOut:
    """Another user's ticket returns 404, the same as a missing one."""
    ticket = await TicketRepository(session).get_for_user(ticket_id, user.id)
    if ticket is None:
        raise AppError(ErrorCode.TICKET_NOT_FOUND, f"Ticket {ticket_id} not found")
    return TicketOut.model_validate(ticket)
