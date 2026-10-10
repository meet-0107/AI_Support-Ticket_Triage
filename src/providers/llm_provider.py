import json
import logging
from typing import Any, Dict, Optional
import httpx

from src.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an AI Support Ticket Triage Classifier.
Analyze the following support ticket subject and body. Classify it into one category, assign a priority, provide a concise summary, and supply a confidence score between 0.0 and 1.0.

Allowed Categories:
- "billing": payment, subscription, refund, invoice, charges, credit card
- "technical": bugs, server errors, crashes, outages, glitches, broken features
- "account": login, password, signup, profile, authentication, reset
- "feature_request": enhancement, suggestions, new capabilities
- "other": general inquiries or questions not matching above

Allowed Priorities:
- "urgent": total blockage, crash, security issue, cannot access critical system
- "high": serious problem, payment dispute, time-sensitive error
- "low": minor issue, question, cosmetic defect, general request

Return ONLY a valid JSON object matching this schema:
{
  "category": "billing" | "technical" | "account" | "feature_request" | "other",
  "priority": "urgent" | "high" | "low",
  "summary": "1-2 sentence summary under 200 characters",
  "confidence": 0.95
}
"""

RETRY_PROMPT_EXTRA = "IMPORTANT: Return strictly valid JSON with exact keys: category, priority, summary, confidence."


class LLMProvider:
    """Unified LLM Provider supporting configured LLM backends."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.provider = (provider or settings.LLM_PROVIDER).lower()
        self.api_key = api_key if api_key is not None else settings.LLM_PROVIDER_API_KEY
        self.model = model if model is not None else settings.LLM_MODEL_NAME
        self.base_url = (base_url or settings.LLM_LOCAL_BASE_URL).rstrip("/")
        self.api_base_url = settings.LLM_API_BASE_URL.rstrip("/")
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS

    async def triage(
        self, subject: str, body: str, is_retry: bool = False
    ) -> Dict[str, Any]:
        if not self.model:
            raise ValueError("LLM_MODEL_NAME is not configured in .env")

        if self.provider == "local":
            return await self._triage_local(subject, body, is_retry)
        else:
            return await self._triage_remote(subject, body, is_retry)

    async def _triage_remote(
        self, subject: str, body: str, is_retry: bool = False
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("LLM_PROVIDER_API_KEY is not configured in .env")

        url = f"{self.api_base_url}/models/{self.model}:generateContent?key={self.api_key}"
        prompt = f"Subject: {subject}\n\nBody:\n{body}"
        instruction = SYSTEM_PROMPT
        if is_retry:
            instruction += f"\n\n{RETRY_PROMPT_EXTRA}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{instruction}\n\nTicket to classify:\n{prompt}"}
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates returned from API")

        content_text = candidates[0]["content"]["parts"][0]["text"].strip()
        parsed = self._clean_and_parse_json(content_text)
        return self._format_result(parsed)

    async def _triage_local(
        self, subject: str, body: str, is_retry: bool = False
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/chat"
        instruction = SYSTEM_PROMPT
        if is_retry:
            instruction += f"\n\n{RETRY_PROMPT_EXTRA}"

        user_content = f"Subject: {subject}\n\nBody:\n{body}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": user_content},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content_text = data.get("message", {}).get("content", "").strip()
        parsed = self._clean_and_parse_json(content_text)
        return self._format_result(parsed)

    @staticmethod
    def _clean_and_parse_json(text: str) -> Dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())

    @staticmethod
    def _format_result(parsed: Dict[str, Any]) -> Dict[str, Any]:
        if "summary" in parsed and len(parsed["summary"]) > 200:
            parsed["summary"] = parsed["summary"][:197] + "..."
        return parsed
