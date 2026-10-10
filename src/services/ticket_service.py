import json
import uuid
from typing import Any, Dict, List, Optional, Tuple
from src.db.database import get_db
from src.schemas.ticket_schema import TicketCreate, TicketStatus
from src.utils.cosine import cosine_similarity


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Converts an sqlite3.Row to a dictionary."""
    if row is None:
        return {}
    return dict(row)


class TicketService:
    """Handles CRUD operations, idempotency, and state transitions for tickets."""

    def __init__(self, default_db_path: Optional[str] = None):
        self.default_db_path = default_db_path

    def create_ticket(
        self,
        tenant_id: str,
        ticket_data: TicketCreate,
        idempotency_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Creates a new queued ticket or returns existing one if idempotency_key matches.

        Returns:
            Tuple of (ticket_dict, is_created_bool).
        """
        target_db = db_path or self.default_db_path

        with get_db(target_db) as conn:
            # 1. Check idempotency if key provided
            if idempotency_key:
                cursor = conn.execute(
                    """
                    SELECT * FROM tickets
                    WHERE tenant_id = ? AND idempotency_key = ?
                    LIMIT 1
                    """,
                    (tenant_id, idempotency_key),
                )
                existing = cursor.fetchone()
                if existing:
                    return _row_to_dict(existing), False

            # 2. Insert new ticket
            new_id = str(uuid.uuid4())
            if isinstance(ticket_data, dict):
                subject = ticket_data["subject"]
                body = ticket_data["body"]
                customer_email = ticket_data.get("customer_email") or ticket_data.get("customerEmail")
                channel = ticket_data.get("channel", "web")
                channel_val = channel.value if hasattr(channel, "value") else str(channel)
            else:
                subject = ticket_data.subject
                body = ticket_data.body
                customer_email = ticket_data.customer_email
                channel_val = (
                    ticket_data.channel.value
                    if hasattr(ticket_data.channel, "value")
                    else str(ticket_data.channel)
                )

            conn.execute(
                """
                INSERT INTO tickets (
                    id, tenant_id, idempotency_key, subject, body,
                    customer_email, channel, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    new_id,
                    tenant_id,
                    idempotency_key,
                    subject,
                    body,
                    customer_email,
                    channel_val,
                    TicketStatus.QUEUED.value,
                ),
            )

            cursor = conn.execute("SELECT * FROM tickets WHERE id = ?", (new_id,))
            created_row = cursor.fetchone()
            return _row_to_dict(created_row), True

    def get_ticket(
        self,
        ticket_id: str,
        tenant_id: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a ticket by ID, optionally scoped to tenant_id for tenant isolation."""
        target_db = db_path or self.default_db_path
        with get_db(target_db) as conn:
            if tenant_id:
                cursor = conn.execute(
                    "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                    (ticket_id, tenant_id),
                )
            else:
                cursor = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
            row = cursor.fetchone()
            return _row_to_dict(row) if row else None

    def update_ticket(
        self,
        ticket_id: str,
        updates: Dict[str, Any],
        db_path: Optional[str] = None,
    ) -> bool:
        """Updates specific fields of a ticket and refreshes updated_at timestamp."""
        if not updates:
            return False

        target_db = db_path or self.default_db_path
        fields = []
        values = []

        for key, value in updates.items():
            fields.append(f"{key} = ?")
            values.append(value)

        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(ticket_id)

        set_clause = ", ".join(fields)
        query = f"UPDATE tickets SET {set_clause} WHERE id = ?"

        with get_db(target_db) as conn:
            cursor = conn.execute(query, tuple(values))
            return cursor.rowcount > 0

    def get_queued_tickets(
        self,
        limit: int = 10,
        db_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetches up to `limit` queued tickets ordered by creation time."""
        target_db = db_path or self.default_db_path
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM tickets
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (TicketStatus.QUEUED.value, limit),
            )
            return [_row_to_dict(row) for row in cursor.fetchall()]

    def mark_processing(
        self,
        ticket_id: str,
        db_path: Optional[str] = None,
    ) -> bool:
        """Atomically transitions a ticket from 'queued' to 'processing'."""
        target_db = db_path or self.default_db_path
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """
                UPDATE tickets
                SET status = ?,
                    processing_started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                (TicketStatus.PROCESSING.value, ticket_id, TicketStatus.QUEUED.value),
            )
            return cursor.rowcount > 0

    def reset_stuck_processing_tickets(
        self,
        timeout_seconds: float = 30.0,
        db_path: Optional[str] = None,
    ) -> int:
        """Resets tickets stuck in 'processing' longer than timeout_seconds back to 'queued'."""
        target_db = db_path or self.default_db_path
        offset_modifier = f"-{int(timeout_seconds)} seconds"
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """
                UPDATE tickets
                SET status = ?,
                    processing_started_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = ?
                  AND processing_started_at <= datetime('now', ?)
                """,
                (
                    TicketStatus.QUEUED.value,
                    TicketStatus.PROCESSING.value,
                    offset_modifier,
                ),
            )
            return cursor.rowcount

    def get_recent_tickets_for_customer(
        self,
        tenant_id: str,
        customer_email: str,
        exclude_ticket_id: str,
        days: int = 7,
        db_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves tickets from the same tenant and customer within the past N days."""
        target_db = db_path or self.default_db_path
        offset_modifier = f"-{int(days)} days"
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """
                SELECT id, subject, body, customer_email, embedding,
                       duplicate_of, duplicate_score, created_at
                FROM tickets
                WHERE tenant_id = ?
                  AND customer_email = ?
                  AND id != ?
                  AND embedding IS NOT NULL
                  AND created_at >= datetime('now', ?)
                ORDER BY created_at DESC
                """,
                (tenant_id, customer_email, exclude_ticket_id, offset_modifier),
            )
            return [_row_to_dict(row) for row in cursor.fetchall()]

    def search_tickets_by_embedding(
        self,
        tenant_id: str,
        query_embedding: List[float],
        limit: int = 5,
        db_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Computes cosine similarity against all embedded tickets for a tenant and ranks them."""
        target_db = db_path or self.default_db_path
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """
                SELECT id, subject, body, category, priority, status, embedding, created_at
                FROM tickets
                WHERE tenant_id = ?
                  AND embedding IS NOT NULL
                """,
                (tenant_id,),
            )
            rows = cursor.fetchall()

        results = []
        for row in rows:
            raw_emb = row["embedding"]
            if not raw_emb:
                continue
            try:
                emb = json.loads(raw_emb)
                score = cosine_similarity(query_embedding, emb)
                ticket_info = _row_to_dict(row)
                ticket_info["similarity"] = round(score, 4)
                ticket_info.pop("embedding", None)  # strip raw embedding
                results.append(ticket_info)
            except Exception:
                continue

        # Sort descending by similarity
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]


ticket_service = TicketService()
