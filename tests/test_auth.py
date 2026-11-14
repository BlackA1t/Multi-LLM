from urllib.parse import parse_qs, urlsplit

import pytest

from multi_llm.auth.openai import OpenAIAuthManager
from multi_llm.errors import AuthenticationError


def test_siwc_authorization_url_has_security_parameters():
    pending, url = OpenAIAuthManager.begin(redirect_uri="http://127.0.0.1:1455/auth/callback", host_id="urn:uuid:test")
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [pending.state]
    assert query["nonce"] == [pending.nonce]
    assert "chatgpt.tokens.use.direct" in query["scope"][0]
    assert query["resource"] == ["https://api.openai.com/v1"]


async def test_state_mismatch_is_rejected_before_http():
    pending, _ = OpenAIAuthManager.begin(redirect_uri="http://127.0.0.1:1455/auth/callback", host_id="urn:uuid:test")
    with pytest.raises(AuthenticationError, match="state mismatch"):
        await OpenAIAuthManager().exchange(pending, {"state": "wrong", "code": "secret"})
