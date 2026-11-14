from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import ProviderAdapter
from ..errors import ProviderResponseError
from ..http_utils import checked_json, rate_headers
from ..models import GenerationRequest, ProviderResult, ResultStatus
from ..validation import require_model, require_text, usage_from


class OpenAISiwcAdapter(ProviderAdapter):
    provider_id = "openai_siwc"
    base_url = "https://api.openai.com/v1"
    unsupported_fields = {
        "background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation",
        "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs",
        "top_p", "truncation", "user", "previous_response_id",
    }

    def __init__(self, access_token: str | None = None, *, token_provider: Any | None = None, client: httpx.AsyncClient | None = None):
        self.access_token = access_token
        self.token_provider = token_provider
        self.client = client or httpx.AsyncClient(timeout=120)

    async def _headers(self) -> dict[str, str]:
        token = await self.token_provider.access_token() if self.token_provider else self.access_token
        if not token:
            raise ProviderResponseError("ChatGPT access token is unavailable")
        return {"authorization": f"Bearer {token}", "content-type": "application/json", "accept": "text/event-stream"}

    @classmethod
    def build_request_body(cls, request: GenerationRequest) -> dict[str, Any]:
        body = {
            "model": request.model_id,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": request.prompt}]}],
            "store": False,
            "stream": True,
        }
        assert not cls.unsupported_fields.intersection(body)
        return body

    async def get_models(self) -> list[dict[str, Any]]:
        data = checked_json(await self.client.get(f"{self.base_url}/models", headers=await self._headers()))
        return [m for m in data.get("models", []) if m.get("visibility") == "list"]

    async def get_status(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "authenticated": bool(self.access_token or self.token_provider), "auth_method": "oauth_siwc"}

    async def generate(self, request: GenerationRequest) -> ProviderResult:
        started = time.perf_counter()
        text_parts: list[str] = []
        completed: dict[str, Any] | None = None
        terminal: str | None = None
        last_headers: httpx.Headers | None = None
        async def consume(event_name: str | None, data_lines: list[str]) -> None:
            nonlocal terminal, completed
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                raise ProviderResponseError("SIWC stream contained malformed JSON") from exc
            if not isinstance(payload, dict):
                raise ProviderResponseError("SIWC stream contained an invalid event")
            kind = payload.get("type") or event_name
            if kind == "response.output_text.delta":
                delta = payload.get("delta")
                if not isinstance(delta, str):
                    raise ProviderResponseError("SIWC text delta is invalid")
                text_parts.append(delta)
            elif kind in {"response.completed", "response.failed", "response.incomplete"}:
                if terminal is not None:
                    raise ProviderResponseError("SIWC stream contained multiple terminal events")
                terminal, completed = kind, payload
            elif kind == "error":
                error = payload.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                raise ProviderResponseError("SIWC stream reported an error", code=code)

        async with self.client.stream("POST", f"{self.base_url}/responses", headers=await self._headers(), json=self.build_request_body(request)) as response:
            last_headers = response.headers
            if response.status_code >= 400:
                body = await response.aread()
                temp = httpx.Response(response.status_code, content=body, headers=response.headers)
                checked_json(temp)
            event_name: str | None = None
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    await consume(event_name, data_lines)
                    data_lines, event_name = [], None
            if data_lines:
                await consume(event_name, data_lines)
        if terminal != "response.completed":
            code = ((completed or {}).get("response") or {}).get("error", {}).get("code")
            raise ProviderResponseError(f"SIWC stream ended with {terminal or 'no terminal event'}", code=code)
        response_obj = (completed or {}).get("response", {})
        if not isinstance(response_obj, dict) or not response_obj.get("id") or response_obj.get("status") != "completed":
            raise ProviderResponseError("SIWC completed event omitted required response fields")
        actual_model = require_model(response_obj.get("model"), request.model_id)
        text = require_text("".join(text_parts))
        usage = usage_from(response_obj.get("usage", {}), "input_tokens", "output_tokens", "total_tokens")
        return ProviderResult(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=actual_model,
            model_family_id=request.model_family_id,
            model_developer=request.model_developer,
            auth_method="oauth_siwc",
            status=ResultStatus.SUCCESS,
            response_text=text,
            raw_response={"id": response_obj.get("id"), "status": response_obj.get("status")},
            duration_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            rate_limits=rate_headers(last_headers or httpx.Headers()),
        )

    async def aclose(self) -> None:
        await self.client.aclose()
        if self.token_provider:
            await self.token_provider.close()
