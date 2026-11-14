import json
import time

import httpx
import pytest

from multi_llm.adapters.openai_siwc import OpenAISiwcAdapter
from multi_llm.cli import adapter_for_provider, async_main, parser
from multi_llm.errors import AuthenticationError
from multi_llm.auth.session import TOKEN_CREDENTIAL


class MemoryCredentials:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, name):
        return self.values.get(name)


def valid_tokens():
    return json.dumps({
        "access_token": "synthetic-access-token",
        "refresh_token": "synthetic-refresh-token",
        "client_id": "oaiapp_test",
        "expires_at": int(time.time()) + 3600,
    })


async def test_openai_catalog_adapter_does_not_require_registry_route(tmp_path):
    credentials = MemoryCredentials({TOKEN_CREDENTIAL: valid_tokens()})
    adapter = adapter_for_provider("openai_siwc", credentials, tmp_path)
    assert isinstance(adapter, OpenAISiwcAdapter)
    adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"models": [
            {"slug": "gpt-test", "display_name": "GPT Test", "visibility": "list"},
            {"slug": "hidden", "visibility": "hidden"},
        ]})
    ))
    models = await adapter.get_models()
    await adapter.aclose()
    assert [model["slug"] for model in models] == ["gpt-test"]


def test_openai_catalog_without_authorization_has_actionable_error(tmp_path):
    with pytest.raises(AuthenticationError, match="auth-chatgpt"):
        adapter_for_provider("openai_siwc", MemoryCredentials(), tmp_path)


async def test_models_command_uses_explicit_provider_not_empty_registry(tmp_path, monkeypatch, capsys):
    class FakeAdapter:
        closed = False

        async def get_models(self):
            return [{"slug": "gpt-test", "visibility": "list"}]

        async def aclose(self):
            self.closed = True

    fake = FakeAdapter()
    monkeypatch.setattr("multi_llm.cli.CredentialStore", lambda: MemoryCredentials())
    monkeypatch.setattr("multi_llm.cli.adapter_for_provider", lambda provider, credentials, root: fake)
    args = parser().parse_args(["--root", str(tmp_path), "models", "openai_siwc"])
    assert await async_main(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["slug"] == "gpt-test"
    assert fake.closed
