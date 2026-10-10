from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class TicketChannel(str, Enum):
    EMAIL = "email"
    WEB = "web"
    CHAT = "chat"


class TicketStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    TRIAGED = "triaged"
    NEEDS_REVIEW = "needs_review"


class TicketCategory(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    FEATURE_REQUEST = "feature_request"
    OTHER = "other"


class TicketPriority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    LOW = "low"


class TriageError(str, Enum):
    SCHEMA_INVALID_AFTER_REPAIR = "schema_invalid_after_repair"
    LOW_CONFIDENCE = "low_confidence"
    TIMEOUT = "timeout"


class TicketCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
    )

    subject: str = Field(..., min_length=3, max_length=150)
    body: str = Field(..., min_length=10, max_length=5000)
    customer_email: EmailStr = Field(..., alias="customerEmail")
    channel: TicketChannel = Field(default=TicketChannel.WEB)

    @field_validator("customer_email", mode="before")
    @classmethod
    def normalize_email(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @property
    def customerEmail(self) -> str:
        return self.customer_email


class TicketCreateResponse(BaseModel):
    id: str
    status: str = "queued"


class TicketResponse(BaseModel):
    id: str
    tenant_id: str
    idempotency_key: Optional[str] = None
    subject: str
    body: str
    customer_email: str
    channel: str
    status: str
    category: Optional[str] = None
    priority: Optional[str] = None
    summary: Optional[str] = None
    confidence: Optional[float] = None
    triage_error: Optional[str] = None
    llm_attempts: int = 0
    duplicate_of: Optional[str] = None
    duplicate_score: Optional[float] = None
    processing_started_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TicketSearchItem(BaseModel):
    id: str
    subject: str
    body: str
    similarity: float
    category: Optional[str] = None
    priority: Optional[str] = None
    status: str
