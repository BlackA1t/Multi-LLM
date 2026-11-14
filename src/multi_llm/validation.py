from __future__ import annotations

from typing import Any

from .errors import ModelMismatch, ProviderResponseError
from .models import TokenUsage


def require_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError("Provider returned no generated text")
    return value


def require_model(actual: Any, requested: str, *, allow_resource_prefix: bool = False) -> str:
    if not isinstance(actual, str) or not actual:
        raise ProviderResponseError("Provider response omitted the actual model ID")
    left, right = actual, requested
    if allow_resource_prefix:
        left, right = left.removeprefix("models/"), right.removeprefix("models/")
    if left != right:
        raise ModelMismatch("Provider returned a model ID different from the verified route")
    return actual


def usage_from(values: dict[str, Any], input_key: str, output_key: str, total_key: str) -> TokenUsage:
    if not isinstance(values, dict):
        raise ProviderResponseError("Provider returned invalid usage data")
    try:
        return TokenUsage(input_tokens=values.get(input_key), output_tokens=values.get(output_key), total_tokens=values.get(total_key))
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError("Provider returned invalid usage data") from exc
