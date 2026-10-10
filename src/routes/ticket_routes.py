from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, status

from src.controllers.ticket_controller import ticket_controller
from src.middleware.auth import get_current_tenant
from src.schemas.ticket_schema import (
    TicketCreate,
    TicketCreateResponse,
    TicketResponse,
    TicketSearchItem,
)

router = APIRouter(prefix="/tickets", tags=["Tickets"])


@router.post(
    "",
    response_model=TicketCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a new support ticket",
)
async def create_ticket(
    payload: TicketCreate,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_current_tenant),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """Submits a new ticket for triage.

    Validates schema, checks idempotency, queues background processing,
    and returns HTTP 202 with ticket ID and queued status.
    """
    effective_idempotency = idempotency_key or x_idempotency_key
    return await ticket_controller.create_ticket(
        payload=payload,
        tenant_id=tenant_id,
        background_tasks=background_tasks,
        idempotency_key=effective_idempotency,
    )


@router.get(
    "/search",
    response_model=List[TicketSearchItem],
    status_code=status.HTTP_200_OK,
    summary="Semantic search for tickets",
)
async def search_tickets(
    q: str = Query(..., min_length=2, max_length=200, description="Search query string"),
    limit: int = Query(5, ge=1, le=20, description="Max results (1-20)"),
    tenant_id: str = Depends(get_current_tenant),
):
    """Searches previously triaged tickets by semantic cosine similarity."""
    return await ticket_controller.search_tickets(
        tenant_id=tenant_id,
        q=q,
        limit=limit,
    )


@router.get(
    "/{ticket_id}",
    response_model=TicketResponse,
    status_code=status.HTTP_200_OK,
    summary="Get ticket details by ID",
)
def get_ticket(
    ticket_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Retrieves a ticket by ID, scoped to the authenticated tenant.
    
    Omits raw embedding vector array. Returns 404 if ticket doesn't exist or belongs to another tenant.
    """
    return ticket_controller.get_ticket_by_id(
        ticket_id=ticket_id,
        tenant_id=tenant_id,
    )
