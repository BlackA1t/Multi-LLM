from __future__ import annotations

import re
import json
from collections.abc import Iterable

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[^\s,;\"}]+"),
]


def redact(value: object, secrets: Iterable[str] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
    return text


def sanitize_json(value: object, secrets: Iterable[str] = ()) -> object:
    """Redact a JSON-compatible structure without changing its shape."""
    sensitive = re.compile(r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token)")
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if sensitive.search(str(key)) else sanitize_json(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item, secrets) for item in value]
    if isinstance(value, str):
        return redact(value, secrets)
    return value
