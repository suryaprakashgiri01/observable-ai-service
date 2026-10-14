from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import Session
from app.api.openapi import AUTH_ERRORS, error_responses
from app.auth.dependencies import CurrentUser
from app.exceptions import AppError, ErrorCode
from app.repositories.tickets import TicketRepository
from app.schemas.tickets import TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


TicketId = Annotated[
    int,
    Path(
        description="`action.ticket.id` from an `/ask` that created a ticket.",
        openapi_examples={"first": {"summary": "Ticket 1", "value": 1}},
    ),
]


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
