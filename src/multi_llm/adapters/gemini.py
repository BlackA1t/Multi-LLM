from __future__ import annotations

import time
from typing import Any

import httpx

from .base import ProviderAdapter
from ..http_utils import checked_json, rate_headers
from ..models import GenerationRequest, ProviderResult, ResultStatus, TokenUsage
from ..validation import require_model, require_text, usage_from


class GeminiAdapter(ProviderAdapter):
    provider_id = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=60)

    async def get_models(self) -> list[dict[str, Any]]:
        response = await self.client.get(f"{self.base_url}/models", headers={"x-goog-api-key": self.api_key})
        data = checked_json(response)
        return [m for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]

    async def get_status(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "authenticated": bool(self.api_key)}

    async def generate(self, request: GenerationRequest) -> ProviderResult:
        started = time.perf_counter()
        model = request.model_id.removeprefix("models/")
        response = await self.client.post(
            f"{self.base_url}/models/{model}:generateContent",
            headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
            json={"contents": [{"role": "user", "parts": [{"text": request.prompt}]}]},
        )
        data = checked_json(response)
        try:
            text = require_text("".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"]))
        except (AttributeError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("Gemini response has no candidate text") from exc
        actual_model = require_model(data.get("modelVersion"), request.model_id, allow_resource_prefix=True)
        usage = usage_from(data.get("usageMetadata", {}), "promptTokenCount", "candidatesTokenCount", "totalTokenCount")
        return ProviderResult(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=actual_model,
            model_family_id=request.model_family_id,
            model_developer=request.model_developer,
            auth_method="api_key",
            status=ResultStatus.SUCCESS,
            response_text=text,
            raw_response={"modelVersion": data.get("modelVersion"), "responseId": data.get("responseId")},
            duration_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            rate_limits=rate_headers(response.headers),
        )

    async def aclose(self) -> None:
        await self.client.aclose()
