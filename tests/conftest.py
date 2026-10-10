import asyncio
import hashlib
import math
import re
from typing import Any, Dict, List
import pytest

from src.providers.embedding_provider import EmbeddingProvider
from src.providers.llm_provider import LLMProvider
from src.services.embedding_service import embedding_service
from src.services.triage_service import triage_service


class FakeEmbedder(EmbeddingProvider):
    """Test fake for embedding generation."""

    DIMENSION: int = 256
    MODEL_NAME: str = "fake-embed-v1"

    def __init__(self):
        self.MODEL_NAME = "fake-embed-v1"

    async def embed_text(self, text: str) -> List[float]:
        tokens = [
            w for w in re.findall(r"[a-z]+", text.lower())
            if len(w) >= 3
        ]
        vec = [0.0] * self.DIMENSION
        for word in tokens:
            idx = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16) % self.DIMENSION
            vec[idx] += 1.0

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        return vec

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [await self.embed_text(t) for t in texts]


class FakeLLM(LLMProvider):
    """Test fake for deterministic LLM triage simulation."""

    def __init__(self, default_delay: float = 0.0):
        self.default_delay = default_delay

    async def triage(
        self, subject: str, body: str, is_retry: bool = False
    ) -> Dict[str, Any]:
        combined = f"{subject} {body}"
        text_lower = combined.lower()

        if "[[SLOW]]" in combined:
            await asyncio.sleep(15.0)
        elif self.default_delay > 0:
            await asyncio.sleep(self.default_delay)

        if "[[ALWAYS_BAD]]" in combined:
            return {"error": "malformed_output"}

        if "[[MALFORMED]]" in combined and not is_retry:
            return {"summary": "Missing required fields"}

        if any(k in text_lower for k in ["bill", "invoice", "payment", "charge", "refund"]):
            category = "billing"
        elif any(k in text_lower for k in ["crash", "bug", "error", "fail", "broken", "technical"]):
            category = "technical"
        elif any(k in text_lower for k in ["account", "login", "password", "auth"]):
            category = "account"
        elif any(k in text_lower for k in ["feature", "request", "suggest"]):
            category = "feature_request"
        else:
            category = "other"

        if "crash" in text_lower or "cannot access" in text_lower:
            priority = "urgent"
        elif "refund" in text_lower or "charged" in text_lower:
            priority = "high"
        else:
            priority = "low"

        confidence = 0.3 if "[[LOW_CONF]]" in combined else 0.95
        summary = f"Summary: {subject.strip()}"
        if len(summary) > 200:
            summary = summary[:197] + "..."

        return {
            "category": category,
            "priority": priority,
            "summary": summary,
            "confidence": confidence,
        }


@pytest.fixture(autouse=True)
def setup_test_fakes(monkeypatch):
    """Automatically configure triage and embedding services with fast fakes for test suite."""
    fake_embedder = FakeEmbedder()
    fake_llm = FakeLLM(default_delay=0.0)

    monkeypatch.setattr(embedding_service, "provider", fake_embedder)
    monkeypatch.setattr(triage_service, "llm_provider", fake_llm)
    monkeypatch.setattr(triage_service.embedding_service, "provider", fake_embedder)
