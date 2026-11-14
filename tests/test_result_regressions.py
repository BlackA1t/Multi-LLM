import json

import httpx
import pytest

from multi_llm.adapters.groq import GroqAdapter
from multi_llm.adapters.openai_siwc import OpenAISiwcAdapter
from multi_llm.errors import ModelMismatch, ProviderResponseError
from multi_llm.models import GenerationRequest


def request(model="qwen/model", family="qwen", developer="Alibaba"):
    return GenerationRequest(prompt="x", model_id=model, model_family_id=family, model_developer=developer)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_provider_model_substitution_is_rejected():
    payload = {"id": "r", "model": "qwen/other", "choices": [{"message": {"content": "OK"}}], "usage": {"total_tokens": 1}}
    adapter = GroqAdapter("secret", client=client(lambda req: httpx.Response(200, json=payload)))
    with pytest.raises(ModelMismatch):
        await adapter.generate(request())
    await adapter.aclose()


def sse(events):
    return httpx.Response(200, text="".join(
        f"event: {kind}\ndata: {json.dumps(payload)}\n\n" for kind, payload in events
    ))


async def test_completed_sse_without_text_is_not_success():
    events = [("response.completed", {"type": "response.completed", "response": {
        "id": "r", "model": "gpt-x", "status": "completed", "usage": {"total_tokens": 1}
    }})]
    adapter = OpenAISiwcAdapter("token", client=client(lambda req: sse(events)))
    with pytest.raises(ProviderResponseError, match="no generated text"):
        await adapter.generate(request("gpt-x", "gpt", "OpenAI"))
    await adapter.aclose()


async def test_invalid_usage_is_rejected():
    payload = {"id": "r", "model": "qwen/model", "choices": [{"message": {"content": "OK"}}], "usage": {"total_tokens": -1}}
    adapter = GroqAdapter("secret", client=client(lambda req: httpx.Response(200, json=payload)))
    with pytest.raises(ProviderResponseError, match="invalid usage"):
        await adapter.generate(request())
    await adapter.aclose()
