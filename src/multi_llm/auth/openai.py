from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.parse import parse_qsl, urlsplit

import httpx
import jwt

from ..errors import AuthenticationError, ReauthenticationRequired

AUTHORIZATION_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


@dataclass(frozen=True)
class PendingAuthorization:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    client_id: str
    host_id: str


class OpenAIAuthManager:
    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=30)

    @staticmethod
    def new_host_id() -> str:
        return f"urn:uuid:{uuid.uuid4()}"

    @staticmethod
    def begin(*, redirect_uri: str, host_id: str, saved_client_id: str | None = None, id_token_hint: str | None = None) -> tuple[PendingAuthorization, str]:
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        client_id = saved_client_id or "dynamic_agent_client"
        params = {
            "client_id": client_id,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if not saved_client_id:
            params["agent_name_hint"] = "Multi-LLM"
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        pending = PendingAuthorization(state, nonce, verifier, redirect_uri, client_id, host_id)
        return pending, f"{AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange(self, pending: PendingAuthorization, callback: dict[str, str]) -> dict[str, Any]:
        if not secrets.compare_digest(callback.get("state", ""), pending.state):
            raise AuthenticationError("OAuth state mismatch")
        if callback.get("error"):
            raise AuthenticationError(f"OAuth authorization failed: {callback['error']}")
        issued_id = callback.get("client_id") or pending.client_id
        if pending.client_id == "dynamic_agent_client" and issued_id == "dynamic_agent_client":
            raise AuthenticationError("Dynamic registration did not return an issued client_id")
        if pending.client_id != "dynamic_agent_client" and issued_id != pending.client_id:
            raise AuthenticationError("OAuth callback returned a different client_id")
        response = await self.client.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "client_id": issued_id, "code": callback.get("code", ""),
            "code_verifier": pending.code_verifier, "redirect_uri": pending.redirect_uri, "resource": RESOURCE,
        })
        if response.status_code != 200:
            raise AuthenticationError(f"Token exchange failed (HTTP {response.status_code})")
        tokens = response.json()
        claims = await self.validate_id_token(tokens.get("id_token", ""), issued_id, pending.nonce)
        granted = set(tokens.get("scope", "").split())
        if "chatgpt.tokens.use.direct" not in granted:
            raise AuthenticationError("ChatGPT plan usage permission was not granted")
        tokens["client_id"] = issued_id
        tokens["identity"] = {"sub": claims["sub"], "email": claims.get("email")}
        tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600))
        return tokens

    async def validate_id_token(self, token: str, audience: str, nonce: str) -> dict[str, Any]:
        if not token:
            raise AuthenticationError("ID token missing")
        jwks_response = await self.client.get(JWKS_URL)
        if jwks_response.status_code != 200:
            raise AuthenticationError("Unable to obtain OpenAI signing keys")
        header = jwt.get_unverified_header(token)
        key_data = next((k for k in jwks_response.json().get("keys", []) if k.get("kid") == header.get("kid")), None)
        if not key_data:
            raise AuthenticationError("ID token signing key not found")
        key = jwt.PyJWK.from_dict(key_data).key
        claims = jwt.decode(token, key=key, algorithms=[header["alg"]], audience=audience, issuer="https://auth.openai.com")
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise AuthenticationError("ID token nonce mismatch")
        return claims

    async def refresh(self, tokens: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": tokens["client_id"],
            "refresh_token": tokens["refresh_token"], "resource": RESOURCE,
        })
        if response.status_code != 200:
            code = None
            try:
                body = response.json()
                code = body.get("error") if isinstance(body.get("error"), str) else body.get("error", {}).get("code")
            except (ValueError, AttributeError):
                pass
            terminal = {
                "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                "refresh_token_invalidated", "refresh_token_reused",
            }
            if code in terminal:
                raise ReauthenticationRequired("ChatGPT session can no longer be refreshed; explicit sign-in is required")
            raise AuthenticationError(f"Token refresh failed (HTTP {response.status_code})")
        try:
            fresh = response.json()
        except ValueError as exc:
            raise AuthenticationError("Token refresh returned malformed JSON") from exc
        if not fresh.get("access_token") or not fresh.get("refresh_token"):
            raise AuthenticationError("Token refresh response omitted required credentials")
        fresh["client_id"] = tokens["client_id"]
        fresh["identity"] = tokens.get("identity", {})
        fresh["id_token"] = fresh.get("id_token") or tokens.get("id_token")
        fresh["scope"] = fresh.get("scope") or tokens.get("scope")
        fresh["expires_at"] = int(time.time()) + int(fresh.get("expires_in", 3600))
        return fresh

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def serialize(tokens: dict[str, Any]) -> str:
        return json.dumps(tokens, separators=(",", ":"))

    async def authorize_loopback(
        self,
        *,
        host_id: str,
        open_browser: Any,
        saved_client_id: str | None = None,
        id_token_hint: str | None = None,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Run the documented 127.0.0.1 OAuth callback flow."""
        loop = asyncio.get_running_loop()
        callback_future: asyncio.Future[dict[str, str]] = loop.create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = (await reader.readline()).decode("ascii", errors="replace").strip()
                parts = request_line.split(" ")
                query = dict(parse_qsl(urlsplit(parts[1]).query)) if len(parts) >= 2 else {"error": "invalid_callback"}
                while await reader.readline() not in (b"\r\n", b"\n", b""):
                    pass
                if not callback_future.done():
                    callback_future.set_result(query)
                body = "Authorization received. You can close this window.".encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
        pending, url = self.begin(
            redirect_uri=redirect_uri,
            host_id=host_id,
            saved_client_id=saved_client_id,
            id_token_hint=id_token_hint,
        )
        open_browser(url)
        try:
            async with server:
                callback = await asyncio.wait_for(callback_future, timeout_seconds)
        finally:
            server.close()
            await server.wait_closed()
        return await self.exchange(pending, callback)
