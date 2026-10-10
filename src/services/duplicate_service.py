import json
from typing import List, Optional, Set, Tuple
from src.config import settings
from src.db.database import get_db
from src.services.ticket_service import ticket_service
from src.utils.cosine import cosine_similarity


class DuplicateService:
    """Detects duplicate tickets within a 7-day window and performs chain resolution."""

    def __init__(self, threshold: Optional[float] = None):
        self.threshold = threshold if threshold is not None else settings.DUPLICATE_THRESHOLD

    def resolve_chain(
        self, candidate_id: str, db_path: Optional[str] = None, max_depth: int = 10
    ) -> str:
        """Resolves duplicate chain to find the canonical root ticket ID.

        If Ticket C is a duplicate of Ticket B, and Ticket B is a duplicate of Ticket A,
        this resolves Ticket C's parent to Ticket A.
        """
        current_id = candidate_id
        visited: Set[str] = set()
        depth = 0

        with get_db(db_path) as conn:
            while current_id and current_id not in visited and depth < max_depth:
                visited.add(current_id)
                depth += 1
                cursor = conn.execute(
                    "SELECT duplicate_of FROM tickets WHERE id = ?", (current_id,)
                )
                row = cursor.fetchone()
                if not row or not row["duplicate_of"]:
                    return current_id
                current_id = str(row["duplicate_of"])

        return current_id

    def find_duplicate(
        self,
        tenant_id: str,
        customer_email: str,
        ticket_id: str,
        ticket_embedding: List[float],
        days: int = 7,
        db_path: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[float]]:
        """Finds the most similar recent ticket for this customer.

        If similarity >= threshold, resolves the chain and returns (canonical_ticket_id, score).
        Otherwise returns (None, None).
        """
        candidates = ticket_service.get_recent_tickets_for_customer(
            tenant_id=tenant_id,
            customer_email=customer_email,
            exclude_ticket_id=ticket_id,
            days=days,
            db_path=db_path,
        )

        best_candidate_id: Optional[str] = None
        max_score: float = -1.0

        for cand in candidates:
            raw_emb = cand.get("embedding")
            if not raw_emb:
                continue
            try:
                cand_emb = (
                    json.loads(raw_emb) if isinstance(raw_emb, str) else raw_emb
                )
                score = cosine_similarity(ticket_embedding, cand_emb)
                if score > max_score:
                    max_score = score
                    best_candidate_id = cand["id"]
            except Exception:
                continue

        if best_candidate_id is not None and max_score >= self.threshold:
            canonical_id = self.resolve_chain(best_candidate_id, db_path=db_path)
            return canonical_id, round(max_score, 4)

        return None, None


duplicate_service = DuplicateService()
