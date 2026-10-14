from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Session
from app.auth.dependencies import CurrentUser
from app.exceptions import AppError, ErrorCode
from app.repositories.tickets import TicketRepository
from app.schemas.tickets import TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/{ticket_id}", response_model=TicketOut, summary="Get one of my tickets")
async def get_ticket(ticket_id: int, user: CurrentUser, session: Session) -> TicketOut:
    ticket = await TicketRepository(session).get_for_user(ticket_id, user.id)
    if ticket is None:
        raise AppError(ErrorCode.TICKET_NOT_FOUND, f"Ticket {ticket_id} not found")
    return TicketOut.model_validate(ticket)
