import asyncio
import json
import logging
from typing import Any, Dict, Optional
from pydantic import ValidationError

from src.config import settings
from src.providers.llm_provider import LLMProvider
from src.schemas.llm_output_schema import LLMOutputSchema
from src.schemas.ticket_schema import TicketStatus, TriageError
from src.services.duplicate_service import DuplicateService, duplicate_service as default_duplicate_service
from src.services.embedding_service import EmbeddingService, embedding_service as default_embedding_service
from src.services.ticket_service import TicketService, ticket_service as default_ticket_service
from src.utils.timeout import async_timeout

logger = logging.getLogger(__name__)


class TriageService:
    """Core triage pipeline orchestrator: Embedding -> Duplicate Detection -> LLM Triage -> Repair Loop."""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        embedding_service: Optional[EmbeddingService] = None,
        duplicate_service: Optional[DuplicateService] = None,
        ticket_service: Optional[TicketService] = None,
        confidence_threshold: Optional[float] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.llm_provider = llm_provider or LLMProvider()

        self.embedding_service = embedding_service or default_embedding_service
        self.duplicate_service = duplicate_service or default_duplicate_service
        self.ticket_service = ticket_service or default_ticket_service
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.CONFIDENCE_THRESHOLD
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.LLM_TIMEOUT_SECONDS
        )

    async def triage_ticket(
        self, ticket_id: str, db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Executes the full triage sequence for a ticket."""
        ticket = self.ticket_service.get_ticket(ticket_id, db_path=db_path)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        subject = ticket["subject"]
        body = ticket["body"]
        tenant_id = ticket["tenant_id"]
        customer_email = ticket["customer_email"]

        # Step 1: Generate Embedding
        embedding, model_name = await self.embedding_service.generate_ticket_embedding(
            subject, body
        )

        # Step 2: Duplicate Detection with Chain Resolution
        duplicate_of, duplicate_score = self.duplicate_service.find_duplicate(
            tenant_id=tenant_id,
            customer_email=customer_email,
            ticket_id=ticket_id,
            ticket_embedding=embedding,
            db_path=db_path,
        )

        # Step 3: LLM Triage with Timeout and Repair Loop
        llm_attempts = 0
        category: Optional[str] = None
        priority: Optional[str] = None
        summary: Optional[str] = None
        confidence: Optional[float] = None
        status: TicketStatus = TicketStatus.TRIAGED
        triage_error: Optional[TriageError] = None

        # Attempt 1
        llm_attempts += 1
        attempt_1_success = False

        try:
            raw_res = await async_timeout(
                self.llm_provider.triage(subject, body, is_retry=False),
                self.timeout_seconds,
            )
            parsed = LLMOutputSchema.model_validate(raw_res)
            category = parsed.category.value
            priority = parsed.priority.value
            summary = parsed.summary
            confidence = parsed.confidence
            attempt_1_success = True
        except asyncio.TimeoutError:
            status = TicketStatus.NEEDS_REVIEW
            triage_error = TriageError.TIMEOUT
        except (ValidationError, Exception) as err:
            logger.warning(f"Triage attempt 1 failed validation for ticket {ticket_id}: {err}")
            # Attempt 2: 1 repair retry on failure
            llm_attempts += 1
            try:
                raw_retry = await async_timeout(
                    self.llm_provider.triage(subject, body, is_retry=True),
                    self.timeout_seconds,
                )
                parsed = LLMOutputSchema.model_validate(raw_retry)
                category = parsed.category.value
                priority = parsed.priority.value
                summary = parsed.summary
                confidence = parsed.confidence
                attempt_1_success = True
            except asyncio.TimeoutError:
                status = TicketStatus.NEEDS_REVIEW
                triage_error = TriageError.TIMEOUT
            except (ValidationError, Exception):
                status = TicketStatus.NEEDS_REVIEW
                triage_error = TriageError.SCHEMA_INVALID_AFTER_REPAIR

        # Step 4: Evaluate confidence if schema is valid
        if attempt_1_success and triage_error is None:
            if confidence is not None and confidence < self.confidence_threshold:
                status = TicketStatus.NEEDS_REVIEW
                triage_error = TriageError.LOW_CONFIDENCE
            else:
                status = TicketStatus.TRIAGED
                triage_error = None

        # Step 5: Persist updates to database
        updates = {
            "status": status.value,
            "category": category,
            "priority": priority,
            "summary": summary,
            "confidence": confidence,
            "triage_error": triage_error.value if triage_error else None,
            "llm_attempts": llm_attempts,
            "embedding": json.dumps(embedding),
            "embedding_model": model_name,
            "duplicate_of": duplicate_of,
            "duplicate_score": duplicate_score,
        }

        self.ticket_service.update_ticket(ticket_id, updates, db_path=db_path)
        return self.ticket_service.get_ticket(ticket_id, db_path=db_path) or {}


triage_service = TriageService()
