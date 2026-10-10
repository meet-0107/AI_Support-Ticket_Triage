from pydantic import BaseModel, ConfigDict, Field
from src.schemas.ticket_schema import TicketCategory, TicketPriority


class LLMOutputSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: TicketCategory
    priority: TicketPriority
    summary: str = Field(..., min_length=1, max_length=200)
    confidence: float = Field(..., ge=0.0, le=1.0)
