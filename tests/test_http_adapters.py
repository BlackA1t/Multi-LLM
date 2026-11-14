import json

import httpx
import pytest

from multi_llm.adapters.gemini import GeminiAdapter
from multi_llm.adapters.groq import GroqAdapter
from multi_llm.adapters.openai_siwc import OpenAISiwcAdapter
from multi_llm.errors import AuthenticationError, ProviderResponseError, QuotaExceeded
from multi_llm.models import GenerationRequest, ResultStatus


def async_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def request(model="qwen/test", family="qwen", developer="Alibaba"):
    return GenerationRequest(prompt="x", model_id=model, model_family_id=family, model_developer=developer)


@pytest.mark.parametrize("status,error", [(401, AuthenticationError), (403, AuthenticationError), (429, QuotaExceeded), (500, ProviderResponseError)])
async def test_normalized_http_errors(status, error):
    client = async_client(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(error):
        await GroqAdapter("secret", client=client).generate(request())


async def test_malformed_json_is_rejected():
    client = async_client(lambda request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(ProviderResponseError):
        await GeminiAdapter("secret", client=client).get_models()


async def test_transport_timeout_propagates_for_run_manager_normalization():
    def timeout(request):
        raise httpx.ReadTimeout("offline timeout", request=request)
    with pytest.raises(httpx.ReadTimeout):
        await GroqAdapter("secret", client=async_client(timeout)).generate(request())


async def test_invalid_model_http_400_is_not_retried_or_replaced():
    client = async_client(lambda request: httpx.Response(400, json={"error": {"code": "model_not_found"}}))
    with pytest.raises(ProviderResponseError) as raised:
        await GroqAdapter("secret", client=client).generate(request("missing"))
    assert raised.value.status_code == 400


async def test_gemini_model_catalog_filters_generation_models():
    payload = {"models": [
        {"name": "models/a", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/b", "supportedGenerationMethods": ["embedContent"]},
    ]}
    models = await GeminiAdapter("secret", client=async_client(lambda request: httpx.Response(200, json=payload))).get_models()
    assert [m["name"] for m in models] == ["models/a"]


def sse_response(events):
    body = "".join(f"event: {kind}\ndata: {json.dumps(payload)}\n\n" for kind, payload in events)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def test_siwc_requires_completed_and_collects_delta():
    events = [
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "OK"}),
        ("response.completed", {"type": "response.completed", "response": {"id": "r", "model": "gpt-x", "status": "completed", "usage": {"total_tokens": 2}}}),
    ]
    adapter = OpenAISiwcAdapter("token", client=async_client(lambda request: sse_response(events)))
    result = await adapter.generate(request("gpt-x", "gpt", "OpenAI"))
    assert result.status == ResultStatus.SUCCESS
    assert result.response_text == "OK"


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", None])
async def test_siwc_rejects_noncompleted_stream(terminal):
    events = [] if terminal is None else [(terminal, {"type": terminal, "response": {"error": {"code": "bad"}}})]
    adapter = OpenAISiwcAdapter("token", client=async_client(lambda request: sse_response(events)))
    with pytest.raises(ProviderResponseError):
        await adapter.generate(request("gpt-x", "gpt", "OpenAI"))


def test_siwc_body_has_required_and_no_unsupported_fields():
    body = OpenAISiwcAdapter.build_request_body(request("gpt-x", "gpt", "OpenAI"))
    assert body["store"] is False and body["stream"] is True
    assert not OpenAISiwcAdapter.unsupported_fields.intersection(body)
