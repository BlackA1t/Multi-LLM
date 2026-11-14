import asyncio
import json
import time

import httpx
import pytest

from multi_llm.auth.openai import OpenAIAuthManager
from multi_llm.auth.session import OpenAITokenProvider, REGISTRATION_CREDENTIAL, TOKEN_CREDENTIAL
from multi_llm.errors import ReauthenticationRequired


class MemoryCredentials:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.atomic_writes = 0

    def get(self, name):
        return self.values.get(name)

    def set_atomic(self, name, value):
        self.atomic_writes += 1
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class FakeAuth:
    def __init__(self, *, terminal=False):
        self.calls = 0
        self.closed = 0
        self.terminal = terminal

    async def refresh(self, tokens):
        self.calls += 1
        await asyncio.sleep(0.02)
        if self.terminal:
            raise ReauthenticationRequired("explicit sign-in is required")
        return {
            **tokens,
            "access_token": "new-access-token",
            "refresh_token": "rotated-refresh-token",
            "expires_at": int(time.time()) + 3600,
        }

    async def close(self):
        self.closed += 1


def expired_tokens():
    return json.dumps({
        "access_token": "expired-access-token", "refresh_token": "old-refresh-token",
        "client_id": "oaiapp_test", "expires_at": 1,
    })


async def test_expired_access_token_refreshes_and_rotates_atomically(tmp_path):
    credentials = MemoryCredentials({TOKEN_CREDENTIAL: expired_tokens()})
    auth = FakeAuth()
    provider = OpenAITokenProvider(credentials, tmp_path / "refresh.lock", auth_factory=lambda: auth)
    assert await provider.access_token() == "new-access-token"
    saved = json.loads(credentials.get(TOKEN_CREDENTIAL))
    assert saved["refresh_token"] == "rotated-refresh-token"
    assert "old-refresh-token" not in credentials.get(TOKEN_CREDENTIAL)
    assert credentials.atomic_writes == 1


async def test_simultaneous_refresh_is_serialized(tmp_path):
    credentials = MemoryCredentials({TOKEN_CREDENTIAL: expired_tokens()})
    auth = FakeAuth()
    first = OpenAITokenProvider(credentials, tmp_path / "refresh.lock", auth_factory=lambda: auth)
    second = OpenAITokenProvider(credentials, tmp_path / "refresh.lock", auth_factory=lambda: auth)
    tokens = await asyncio.gather(first.access_token(), second.access_token(), first.access_token())
    assert tokens == ["new-access-token"] * 3
    assert auth.calls == 1


async def test_terminal_refresh_requires_explicit_login_and_preserves_registration(tmp_path):
    credentials = MemoryCredentials({
        TOKEN_CREDENTIAL: expired_tokens(),
        REGISTRATION_CREDENTIAL: json.dumps({"client_id": "oaiapp_test"}),
    })
    auth = FakeAuth(terminal=True)
    provider = OpenAITokenProvider(credentials, tmp_path / "refresh.lock", auth_factory=lambda: auth)
    with pytest.raises(ReauthenticationRequired, match="sign-in"):
        await provider.access_token()
    assert credentials.get(TOKEN_CREDENTIAL) is None
    assert json.loads(credentials.get(REGISTRATION_CREDENTIAL))["client_id"] == "oaiapp_test"
    with pytest.raises(ReauthenticationRequired):
        await provider.access_token()
    assert auth.calls == 1


async def test_invalid_grant_error_does_not_leak_refresh_token():
    secret = "refresh-super-secret-value"
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": "invalid_grant", "diagnostic": secret})
    ))
    manager = OpenAIAuthManager(client=client)
    with pytest.raises(ReauthenticationRequired) as raised:
        await manager.refresh({"client_id": "oaiapp_test", "refresh_token": secret})
    assert secret not in str(raised.value)
    await manager.close()
