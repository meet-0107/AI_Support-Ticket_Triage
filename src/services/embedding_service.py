from typing import List, Optional, Tuple
from src.config import settings
from src.providers.embedding_provider import EmbeddingProvider


class EmbeddingService:
    """Service to produce normalized vector embeddings for tickets and queries."""

    def __init__(self, provider: Optional[EmbeddingProvider] = None):
        self.provider = provider or EmbeddingProvider()

    @property
    def model_name(self) -> str:
        return getattr(self.provider, "MODEL_NAME", None) or settings.LLM_EMBEDDING_MODEL_NAME

    async def generate_ticket_embedding(
        self, subject: str, body: str
    ) -> Tuple[List[float], str]:
        """Generates embedding for combined subject and body."""
        combined_text = f"{subject}\n\n{body}"
        vector = await self.provider.embed_text(combined_text)
        return vector, self.model_name

    async def generate_query_embedding(self, query: str) -> List[float]:
        """Generates embedding for a search query string."""
        return await self.provider.embed_text(query)


embedding_service = EmbeddingService()
