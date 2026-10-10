import math
import logging
from typing import List, Optional
import httpx

from src.config import settings

logger = logging.getLogger(__name__)


def l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        return [x / norm for x in vec]
    return vec


class EmbeddingProvider:
    """Unified Embedding Provider supporting configured embedding backends."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.provider = (provider or settings.effective_embedding_provider).lower()
        self.api_key = api_key if api_key is not None else settings.LLM_PROVIDER_API_KEY
        self.model = model if model is not None else settings.LLM_EMBEDDING_MODEL_NAME
        self.base_url = (base_url or settings.LLM_LOCAL_BASE_URL).rstrip("/")
        self.api_base_url = settings.LLM_API_BASE_URL.rstrip("/")
        self.timeout = timeout if timeout is not None else 10.0
        self.MODEL_NAME: str = self.model

    async def embed_text(self, text: str) -> List[float]:
        if not self.model:
            raise ValueError("LLM_EMBEDDING_MODEL_NAME is not configured in .env")

        if self.provider == "local":
            return await self._embed_local(text)
        else:
            return await self._embed_remote(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return [await self.embed_text(t) for t in texts]

    async def _embed_remote(self, text: str) -> List[float]:
        if not self.api_key:
            raise ValueError("LLM_PROVIDER_API_KEY is not configured in .env")

        url = f"{self.api_base_url}/models/{self.model}:embedContent?key={self.api_key}"
        payload = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        values = data.get("embedding", {}).get("values", [])
        if not values:
            raise ValueError(f"No embedding values returned from API: {data}")

        return l2_normalize(values)

    async def _embed_local(self, text: str) -> List[float]:
        url = f"{self.base_url}/api/embeddings"
        payload = {
            "model": self.model,
            "prompt": text,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        embedding = data.get("embedding", [])
        if not embedding:
            raise ValueError(f"No embedding returned from API: {data}")

        return l2_normalize(embedding)
