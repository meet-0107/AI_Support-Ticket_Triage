import asyncio
import logging
from typing import Any, Dict, Optional
from src.config import settings
from src.services.ticket_service import TicketService, ticket_service as default_ticket_service
from src.services.triage_service import TriageService, triage_service as default_triage_service

logger = logging.getLogger(__name__)


class TicketWorker:
    """Async background worker polling queued tickets and recovering stuck processing tickets."""

    def __init__(
        self,
        ticket_service: Optional[TicketService] = None,
        triage_service: Optional[TriageService] = None,
        poll_interval: float = 1.0,
        stuck_timeout_seconds: Optional[float] = None,
        db_path: Optional[str] = None,
    ):
        self.ticket_service = ticket_service or default_ticket_service
        self.triage_service = triage_service or default_triage_service
        self.poll_interval = poll_interval
        self.stuck_timeout_seconds = (
            stuck_timeout_seconds
            if stuck_timeout_seconds is not None
            else settings.STUCK_TIMEOUT_SECONDS
        )
        self.db_path = db_path
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

    def recover_stuck_tickets(self) -> int:
        """Resets tickets stuck in 'processing' status longer than stuck_timeout_seconds."""
        recovered_count = self.ticket_service.reset_stuck_processing_tickets(
            timeout_seconds=self.stuck_timeout_seconds, db_path=self.db_path
        )
        if recovered_count > 0:
            logger.warning(
                f"Recovered {recovered_count} stuck tickets back to 'queued' state."
            )
        return recovered_count

    async def process_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Transitions ticket to processing and executes triage."""
        marked = self.ticket_service.mark_processing(ticket_id, db_path=self.db_path)
        if not marked:
            logger.info(f"Ticket {ticket_id} was already taken or not in queued status.")
            return None

        try:
            return await self.triage_service.triage_ticket(
                ticket_id, db_path=self.db_path
            )
        except Exception as e:
            logger.error(f"Failed to triage ticket {ticket_id}: {e}", exc_info=True)
            return None

    async def process_queued_batch(self, batch_size: int = 10) -> int:
        """Runs stuck ticket recovery and processes up to batch_size queued tickets."""
        self.recover_stuck_tickets()

        queued_tickets = self.ticket_service.get_queued_tickets(
            limit=batch_size, db_path=self.db_path
        )
        if not queued_tickets:
            return 0

        processed_count = 0
        for ticket in queued_tickets:
            res = await self.process_ticket(ticket["id"])
            if res is not None:
                processed_count += 1

        return processed_count

    async def _run_loop(self) -> None:
        """Main background worker polling loop."""
        logger.info("Ticket worker started polling loop.")
        while self.is_running:
            try:
                processed = await self.process_queued_batch()
                if processed == 0:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.info("Ticket worker received cancellation signal.")
                break
            except Exception as e:
                logger.error(f"Error in ticket worker loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

    def start(self) -> asyncio.Task:
        """Starts worker loop as an asyncio background task."""
        if self.is_running:
            return self._task

        self.is_running = True
        self._task = asyncio.create_task(self._run_loop())
        return self._task

    async def stop(self) -> None:
        """Signals worker to stop and waits for task completion."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Ticket worker stopped.")


ticket_worker = TicketWorker()


def dispatch_to_celery(ticket_id: str, db_path: Optional[str] = None):
    """Enqueues a ticket triage task into the Celery task queue."""
    from src.workers.celery_worker import triage_ticket_task
    return triage_ticket_task.delay(ticket_id, db_path)

