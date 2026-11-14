from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from ..errors import ReauthenticationRequired
from ..secrets import CredentialStore
from .openai import OpenAIAuthManager

TOKEN_CREDENTIAL = "openai_siwc_tokens"
REGISTRATION_CREDENTIAL = "openai_siwc_registration"
@contextmanager
def _exclusive_file(path: Path) -> Iterator[None]:
    """Cross-process refresh lock for Windows; tests may use the same primitive."""
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class OpenAITokenProvider:
    def __init__(
        self,
        credentials: CredentialStore,
        lock_path: Path,
        *,
        auth_factory: Callable[[], OpenAIAuthManager] = OpenAIAuthManager,
        clock: Callable[[], float] = time.time,
        refresh_skew_seconds: int = 60,
    ):
        self.credentials = credentials
        self.lock_path = lock_path
        self.auth_factory = auth_factory
        self.clock = clock
        self.refresh_skew_seconds = refresh_skew_seconds
        self._lock = asyncio.Lock()

    def _load(self) -> dict:
        serialized = self.credentials.get(TOKEN_CREDENTIAL)
        if not serialized:
            raise ReauthenticationRequired("ChatGPT sign-in is required")
        try:
            return json.loads(serialized)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ReauthenticationRequired("Stored ChatGPT credentials are invalid; sign in again") from exc

    def _valid(self, tokens: dict) -> bool:
        return bool(tokens.get("access_token")) and float(tokens.get("expires_at", 0)) > self.clock() + self.refresh_skew_seconds

    async def access_token(self) -> str:
        tokens = self._load()
        if self._valid(tokens):
            return str(tokens["access_token"])
        async with self._lock:
            return await asyncio.to_thread(self._refresh_under_process_lock)

    def _refresh_under_process_lock(self) -> str:
        with _exclusive_file(self.lock_path):
            tokens = self._load()
            if self._valid(tokens):
                return str(tokens["access_token"])
            if not tokens.get("refresh_token") or not tokens.get("client_id"):
                raise ReauthenticationRequired("Renewable ChatGPT credentials are incomplete; sign in again")
            try:
                fresh = asyncio.run(self._refresh_once(tokens))
            except ReauthenticationRequired:
                self.credentials.delete(TOKEN_CREDENTIAL)
                raise
            self.credentials.set_atomic(TOKEN_CREDENTIAL, OpenAIAuthManager.serialize(fresh))
            return str(fresh["access_token"])

    async def _refresh_once(self, tokens: dict) -> dict:
        manager = self.auth_factory()
        try:
            return await manager.refresh(tokens)
        finally:
            await manager.close()

    async def close(self) -> None:
        return None
