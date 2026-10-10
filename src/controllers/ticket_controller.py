from typing import Any, Dict, List, Optional
from fastapi import BackgroundTasks, HTTPException, status

from src.schemas.ticket_schema import TicketCreate, TicketResponse, TicketSearchItem
from src.services.embedding_service import embedding_service
from src.services.ticket_service import ticket_service
from src.workers.ticket_worker import ticket_worker


class TicketController:
    """Controller handling ticket endpoints, validation, and async execution."""

    def __init__(
        self,
        t_service=None,
        e_service=None,
        worker=None,
    ):
        self.ticket_service = t_service or ticket_service
        self.embedding_service = e_service or embedding_service
        self.ticket_worker = worker or ticket_worker

    async def create_ticket(
        self,
        payload: TicketCreate,
        tenant_id: str,
        background_tasks: BackgroundTasks,
        idempotency_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a ticket and queues background processing. Returns HTTP 202."""
        ticket, is_created = self.ticket_service.create_ticket(
            tenant_id=tenant_id,
            ticket_data=payload,
            idempotency_key=idempotency_key,
            db_path=db_path,
        )

        # Only queue background processing if this is a newly created ticket
        if is_created:
            background_tasks.add_task(
                self.ticket_worker.process_ticket, ticket["id"]
            )

        return {
            "id": ticket["id"],
            "status": ticket["status"],
        }

    def get_ticket_by_id(
        self,
        ticket_id: str,
        tenant_id: str,
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves a ticket by ID, enforcing tenant isolation.
        
        Returns ticket without raw embedding array, or raises 404.
        """
        ticket = self.ticket_service.get_ticket(
            ticket_id=ticket_id, tenant_id=tenant_id, db_path=db_path
        )
        if not ticket:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ticket not found",
            )

        # Omit raw embedding vector array from API response
        ticket_copy = dict(ticket)
        ticket_copy.pop("embedding", None)
        return ticket_copy

    async def search_tickets(
        self,
        tenant_id: str,
        q: str,
        limit: int = 5,
        db_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Validates query and limit, embeds query, and returns ranked ticket results."""
        cleaned_query = q.strip()
        if len(cleaned_query) < 2 or len(cleaned_query) > 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query must be between 2 and 200 characters",
            )

        if limit < 1 or limit > 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit must be between 1 and 20",
            )

        # Generate query embedding
        query_embedding = await self.embedding_service.generate_query_embedding(
            cleaned_query
        )

        # Rank tickets
        ranked_results = self.ticket_service.search_tickets_by_embedding(
            tenant_id=tenant_id,
            query_embedding=query_embedding,
            limit=limit,
            db_path=db_path,
        )

        return ranked_results


ticket_controller = TicketController()
