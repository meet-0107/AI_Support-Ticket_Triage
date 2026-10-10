import asyncio
import json
import math
import pytest
import httpx

from src.utils.cosine import cosine_similarity
from src.utils.timeout import async_timeout
from src.providers.llm_provider import LLMProvider
from src.providers.embedding_provider import EmbeddingProvider


# ==================== Cosine Similarity Tests ====================

def test_cosine_similarity_identical_vectors():
    v1 = [1.0, 0.0, 0.5]
    assert pytest.approx(cosine_similarity(v1, v1), 1e-6) == 1.0


def test_cosine_similarity_orthogonal_vectors():
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2), 1e-6) == 0.0


def test_cosine_similarity_zero_vector():
    v1 = [0.0, 0.0, 0.0]
    v2 = [1.0, 2.0, 3.0]
    assert cosine_similarity(v1, v2) == 0.0


def test_cosine_similarity_dimension_mismatch():
    v1 = [1.0, 2.0]
    v2 = [1.0, 2.0, 3.0]
    with pytest.raises(ValueError, match="Vector dimensions must match"):
        cosine_similarity(v1, v2)


# ==================== Async Timeout Tests ====================

@pytest.mark.asyncio
async def test_async_timeout_success():
    async def quick_coro():
        return "completed"

    result = await async_timeout(quick_coro(), 1.0)
    assert result == "completed"


@pytest.mark.asyncio
async def test_async_timeout_exceeded():
    async def slow_coro():
        await asyncio.sleep(0.5)
        return "too slow"

    with pytest.raises(asyncio.TimeoutError):
        await async_timeout(slow_coro(), 0.05)


# ==================== LLM Provider Tests ====================

@pytest.mark.asyncio
async def test_llm_provider_remote_success(monkeypatch):
    mock_payload = {
        "category": "billing",
        "priority": "high",
        "summary": "Customer charged twice for subscription.",
        "confidence": 0.98,
    }

    async def mock_post(self, url, *args, **kwargs):
        data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(mock_payload)}
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    llm = LLMProvider(provider="remote", api_key="test-key", model="test-llm-model")
    result = await llm.triage("Double charge", "I was billed twice.")
    assert result["category"] == "billing"
    assert result["priority"] == "high"
    assert result["confidence"] == 0.98


@pytest.mark.asyncio
async def test_llm_provider_missing_api_key():
    llm = LLMProvider(provider="remote", api_key="", model="test-llm-model")
    with pytest.raises(ValueError, match="LLM_PROVIDER_API_KEY is not configured"):
        await llm.triage("Subject", "Body")


@pytest.mark.asyncio
async def test_llm_provider_missing_model_name():
    llm = LLMProvider(provider="remote", api_key="test-key", model="")
    with pytest.raises(ValueError, match="LLM_MODEL_NAME is not configured"):
        await llm.triage("Subject", "Body")


@pytest.mark.asyncio
async def test_llm_provider_local_success(monkeypatch):
    mock_payload = {
        "category": "technical",
        "priority": "urgent",
        "summary": "Database server crashed.",
        "confidence": 0.92,
    }

    async def mock_post(self, url, *args, **kwargs):
        data = {
            "message": {
                "content": json.dumps(mock_payload)
            }
        }
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    llm = LLMProvider(provider="local", base_url="http://localhost:11434", model="test-llm-model")
    res = await llm.triage("DB crash", "The server went down.")
    assert res["category"] == "technical"
    assert res["priority"] == "urgent"
    assert res["confidence"] == 0.92


@pytest.mark.asyncio
async def test_llm_provider_local_retry(monkeypatch):
    captured_payload = {}

    async def mock_post(self, url, *args, **kwargs):
        nonlocal captured_payload
        captured_payload = kwargs.get("json", {})
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps({"category": "billing", "priority": "high", "summary": "Payment", "confidence": 0.9})}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    llm = LLMProvider(provider="local", base_url="http://localhost:11434", model="test-llm-model")
    await llm.triage("Payment failed", "Card error", is_retry=True)
    system_msg = captured_payload["messages"][0]["content"]
    assert "IMPORTANT: Return strictly valid JSON" in system_msg


# ==================== Embedding Provider Tests ====================

@pytest.mark.asyncio
async def test_embedding_provider_remote(monkeypatch):
    raw_vec = [3.0, 4.0]

    async def mock_post(self, url, *args, **kwargs):
        data = {"embedding": {"values": raw_vec}}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    embedder = EmbeddingProvider(provider="remote", api_key="test-key", model="test-embed-model")
    vec = await embedder.embed_text("Login problem")
    norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(norm, 1e-6) == 1.0
    assert pytest.approx(vec[0], 1e-6) == 0.6
    assert pytest.approx(vec[1], 1e-6) == 0.8


@pytest.mark.asyncio
async def test_embedding_provider_missing_model_name():
    embedder = EmbeddingProvider(provider="remote", api_key="test-key", model="")
    with pytest.raises(ValueError, match="LLM_EMBEDDING_MODEL_NAME is not configured"):
        await embedder.embed_text("Sample query")


@pytest.mark.asyncio
async def test_embedding_provider_local(monkeypatch):
    raw_vec = [1.0, 1.0, 1.0, 1.0]

    async def mock_post(self, url, *args, **kwargs):
        data = {"embedding": raw_vec}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    embedder = EmbeddingProvider(provider="local", base_url="http://localhost:11434", model="test-embed-model")
    vec = await embedder.embed_text("Sample query")
    norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(norm, 1e-6) == 1.0
    assert embedder.MODEL_NAME == "test-embed-model"


@pytest.mark.asyncio
async def test_embedding_provider_batch(monkeypatch):
    async def mock_post(self, url, *args, **kwargs):
        data = {"embedding": [0.0, 5.0]}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    embedder = EmbeddingProvider(provider="local", base_url="http://localhost:11434", model="test-embed-model")
    batch = await embedder.embed_batch(["alpha", "beta"])
    assert len(batch) == 2
    assert pytest.approx(batch[0][1], 1e-6) == 1.0
