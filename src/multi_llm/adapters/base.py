from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import GenerationRequest, ProviderResult


class ProviderAdapter(ABC):
    provider_id: str

    @abstractmethod
    async def get_models(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> ProviderResult: ...

    async def get_rate_limits(self) -> dict[str, Any]:
        return {"known": False}

    async def aclose(self) -> None:
        return None
