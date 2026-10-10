import asyncio
import logging
from typing import Any, Dict, Optional

from src.services.ticket_service import ticket_service
from src.services.triage_service import triage_service
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Executes an async coroutine synchronously inside Celery worker."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@celery_app.task(name="triage_ticket_task", bind=True, max_retries=3)
def triage_ticket_task(
    self, ticket_id: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Celery task to transition ticket to processing and execute full triage pipeline."""
    logger.info(f"Celery task triage_ticket_task received for ticket_id={ticket_id}")
    
    # 1. Attempt to mark ticket as processing
    marked = ticket_service.mark_processing(ticket_id, db_path=db_path)
    if not marked:
        # Check current status
        existing = ticket_service.get_ticket(ticket_id, db_path=db_path)
        if not existing or existing.get("status") != "queued":
            logger.info(f"Ticket {ticket_id} already processing or completed.")
            return existing

    # 2. Run async triage pipeline
    try:
        result = _run_async(triage_service.triage_ticket(ticket_id, db_path=db_path))
        logger.info(f"Celery task successfully triaged ticket_id={ticket_id}")
        return result
    except Exception as exc:
        logger.error(f"Error triaging ticket {ticket_id} in Celery: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=2)


@celery_app.task(name="recover_stuck_tickets_task")
def recover_stuck_tickets_task(
    timeout_seconds: float = 30.0, db_path: Optional[str] = None
) -> int:
    """Celery task (or scheduled periodic task) to recover tickets stuck in processing."""
    recovered = ticket_service.reset_stuck_processing_tickets(
        timeout_seconds=timeout_seconds, db_path=db_path
    )
    if recovered > 0:
        logger.warning(f"Celery task recovered {recovered} stuck tickets.")
    return recovered


@celery_app.task(name="process_queued_batch_task")
def process_queued_batch_task(
    batch_size: int = 10, db_path: Optional[str] = None
) -> int:
    """Dispatches queued tickets to individual Celery worker tasks."""
    recover_stuck_tickets_task(timeout_seconds=30.0, db_path=db_path)

    queued_tickets = ticket_service.get_queued_tickets(limit=batch_size, db_path=db_path)
    for t in queued_tickets:
        triage_ticket_task.delay(t["id"], db_path)

    return len(queued_tickets)
