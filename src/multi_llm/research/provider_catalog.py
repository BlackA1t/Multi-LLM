from __future__ import annotations

from .models import Assignment


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

R0_ASSIGNMENTS: tuple[Assignment, ...] = (
    Assignment(provider_id="chatgpt", role="r0_chatgpt"),
    Assignment(provider_id="gemini", role="r0_gemini"),
    Assignment(provider_id="deepseek", role="r0_deepseek"),
    Assignment(provider_id="qwen", role="r0_qwen"),
    Assignment(provider_id="grok", role="r0_grok"),
    Assignment(provider_id="copilot", role="r0_copilot"),
    Assignment(provider_id="gigachat", role="r0_gigachat"),
    Assignment(provider_id="alice", role="r0_alice"),
)

PERPLEXITY_SEARCH_ASSIGNMENT = Assignment(
    provider_id="perplexity", role="search_perplexity"
)


def is_approved_provider(provider_id: str) -> bool:
    return provider_id in APPROVED_PROVIDER_IDS
