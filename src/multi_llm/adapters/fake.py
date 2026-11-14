from __future__ import annotations

import asyncio
from typing import Any

from .base import ProviderAdapter
from ..models import GenerationRequest, ProviderResult, ResultStatus, TokenUsage


class FakeAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, family: str, developer: str, *, delay: float = 0.01, status: ResultStatus = ResultStatus.SUCCESS):
        self.provider_id = provider_id
        self.family = family
        self.developer = developer
        self.delay = delay
        self.result_status = status
        self.active = 0
        self.max_active = 0

    async def get_models(self) -> list[dict[str, Any]]:
        return [{"id": f"{self.provider_id}-fake", "family": self.family}]

    async def get_status(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "authenticated": True, "fake": True}

    async def generate(self, request: GenerationRequest) -> ProviderResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        ok = self.result_status == ResultStatus.SUCCESS
        return ProviderResult(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=request.model_id,
            model_family_id=request.model_family_id,
            model_developer=request.model_developer,
            auth_method="fake",
            status=self.result_status,
            response_text=f"fake:{self.provider_id}:{request.prompt}" if ok else None,
            error=None if ok else f"simulated {self.result_status}",
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2) if ok else None,
        )
