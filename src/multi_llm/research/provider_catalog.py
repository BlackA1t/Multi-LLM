from __future__ import annotations


APPROVED_PROVIDER_IDS = frozenset({
    "chatgpt",
    "perplexity",
    "deepseek",
    "qwen",
    "gigachat",
    "alice",
    "grok",
    "gemini",
    "copilot",
})


def is_approved_provider(provider_id: str) -> bool:
    return provider_id in APPROVED_PROVIDER_IDS
