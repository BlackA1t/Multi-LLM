from __future__ import annotations

import time
from typing import Any

import httpx

from .base import ProviderAdapter
from ..http_utils import checked_json, rate_headers
from ..models import GenerationRequest, ProviderResult, ResultStatus
from ..validation import require_model, require_text, usage_from


class GroqAdapter(ProviderAdapter):
    provider_id = "groq"
    base_url = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=60)

    @property
    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}

    async def get_models(self) -> list[dict[str, Any]]:
        return list(checked_json(await self.client.get(f"{self.base_url}/models", headers=self.headers)).get("data", []))

    async def get_status(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "authenticated": bool(self.api_key)}

    async def generate(self, request: GenerationRequest) -> ProviderResult:
        started = time.perf_counter()
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self.headers,
            json={"model": request.model_id, "messages": [{"role": "user", "content": request.prompt}]},
        )
        data = checked_json(response)
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ValueError("Groq response omitted completion ID")
        try:
            text = require_text(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Groq response has no completion text") from exc
        actual_model = require_model(data.get("model"), request.model_id)
        usage = usage_from(data.get("usage", {}), "prompt_tokens", "completion_tokens", "total_tokens")
        return ProviderResult(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=actual_model,
            model_family_id=request.model_family_id,
            model_developer=request.model_developer,
            auth_method="api_key",
            status=ResultStatus.SUCCESS,
            response_text=text,
            raw_response={"id": data.get("id"), "system_fingerprint": data.get("system_fingerprint")},
            duration_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            rate_limits=rate_headers(response.headers),
        )

    async def aclose(self) -> None:
        await self.client.aclose()
