from __future__ import annotations

from typing import Any

import httpx

from .errors import AuthenticationError, ProviderResponseError, QuotaExceeded


def checked_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in (401, 403):
        raise AuthenticationError(f"Provider rejected credentials or permission (HTTP {response.status_code})")
    if response.status_code == 429:
        raise QuotaExceeded("Provider quota or rate limit reached", retry_after=response.headers.get("retry-after"))
    if response.status_code >= 400:
        raise ProviderResponseError(f"Provider HTTP error {response.status_code}", status_code=response.status_code)
    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderResponseError("Provider returned malformed JSON", status_code=response.status_code) from exc
    if not isinstance(data, dict):
        raise ProviderResponseError("Provider returned an unexpected JSON shape", status_code=response.status_code)
    return data


def rate_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-ratelimit-") or k.lower() == "retry-after"}
