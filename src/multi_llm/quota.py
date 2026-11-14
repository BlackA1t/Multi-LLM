from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from .errors import QuotaExceeded


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class QuotaState:
    requests: int = 0
    tokens: int = 0
    reserved_requests: int = 0
    reserved_tokens: int = 0
    last_called_at: datetime | None = None
    blocked_until: datetime | None = None
    request_limit: int | None = None
    token_limit: int | None = None
    reset_at: datetime | None = None
    known_limits: dict[str, str | int] = field(default_factory=dict)


class QuotaManager:
    """SQLite-backed conservative quota ledger with atomic reservations."""

    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS quota_states (
                provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                requests_used INTEGER NOT NULL DEFAULT 0, tokens_used INTEGER NOT NULL DEFAULT 0,
                reserved_requests INTEGER NOT NULL DEFAULT 0, reserved_tokens INTEGER NOT NULL DEFAULT 0,
                last_called_at TEXT, blocked_until TEXT, request_limit INTEGER, token_limit INTEGER,
                reset_at TEXT, known_limits_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(provider_id, model_id)
            );
            CREATE TABLE IF NOT EXISTS quota_reservations (
                reservation_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                reserved_requests INTEGER NOT NULL, reserved_tokens INTEGER NOT NULL, created_at TEXT NOT NULL
            );
        """)

    def configure(self, provider_id: str, model_id: str, *, request_limit: int | None, token_limit: int | None, reset_at: datetime | None) -> None:
        if request_limit is None and token_limit is None:
            return
        if (request_limit is not None and request_limit <= 0) or (token_limit is not None and token_limit <= 0):
            raise ValueError("Quota limits must be positive")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(provider_id, model_id)
                safe_reset = reset_at if reset_at and reset_at > _utcnow() else None
                self._db.execute(
                    "UPDATE quota_states SET request_limit=?,token_limit=?,reset_at=COALESCE(?,reset_at) WHERE provider_id=? AND model_id=?",
                    (request_limit, token_limit, _iso(safe_reset), provider_id, model_id),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def _ensure(self, provider_id: str, model_id: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO quota_states(provider_id,model_id) VALUES (?,?)", (provider_id, model_id))

    def _reset_if_due(self, provider_id: str, model_id: str, now: datetime) -> None:
        row = self._db.execute("SELECT reset_at FROM quota_states WHERE provider_id=? AND model_id=?", (provider_id, model_id)).fetchone()
        reset_at = _dt(row["reset_at"]) if row else None
        if reset_at and reset_at <= now:
            self._db.execute(
                "UPDATE quota_states SET requests_used=0,tokens_used=0,reserved_requests=0,reserved_tokens=0,blocked_until=NULL,reset_at=NULL WHERE provider_id=? AND model_id=?",
                (provider_id, model_id),
            )
            self._db.execute("DELETE FROM quota_reservations WHERE provider_id=? AND model_id=?", (provider_id, model_id))

    def state(self, provider_id: str, model_id: str) -> QuotaState:
        with self._lock:
            self._ensure(provider_id, model_id)
            row = self._db.execute("SELECT * FROM quota_states WHERE provider_id=? AND model_id=?", (provider_id, model_id)).fetchone()
        return QuotaState(
            requests=row["requests_used"], tokens=row["tokens_used"], reserved_requests=row["reserved_requests"],
            reserved_tokens=row["reserved_tokens"], last_called_at=_dt(row["last_called_at"]), blocked_until=_dt(row["blocked_until"]),
            request_limit=row["request_limit"], token_limit=row["token_limit"], reset_at=_dt(row["reset_at"]),
            known_limits=json.loads(row["known_limits_json"]),
        )

    def available(self, provider_id: str, model_id: str, *, estimated_tokens: int = 1) -> bool:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(provider_id, model_id)
                self._reset_if_due(provider_id, model_id, _utcnow())
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        state = self.state(provider_id, model_id)
        now = _utcnow()
        if state.blocked_until and state.blocked_until > now:
            return False
        if state.request_limit is None and state.token_limit is None:
            return False
        if state.request_limit is not None and state.requests + state.reserved_requests + 1 > state.request_limit:
            return False
        if state.token_limit is not None and state.tokens + state.reserved_tokens + estimated_tokens > state.token_limit:
            return False
        return True

    def reserve(self, provider_id: str, model_id: str, *, estimated_tokens: int) -> str:
        if estimated_tokens <= 0:
            raise ValueError("estimated_tokens must be positive")
        reservation_id, now = str(uuid.uuid4()), _utcnow()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(provider_id, model_id)
                self._reset_if_due(provider_id, model_id, now)
                row = self._db.execute("SELECT * FROM quota_states WHERE provider_id=? AND model_id=?", (provider_id, model_id)).fetchone()
                blocked = _dt(row["blocked_until"])
                if blocked and blocked > now:
                    raise QuotaExceeded("Persisted Retry-After window has not elapsed")
                if row["request_limit"] is None and row["token_limit"] is None:
                    raise QuotaExceeded("No verified numeric quota is configured")
                if row["request_limit"] is not None and row["requests_used"] + row["reserved_requests"] + 1 > row["request_limit"]:
                    raise QuotaExceeded("Verified request quota would be exceeded")
                if row["token_limit"] is not None and row["tokens_used"] + row["reserved_tokens"] + estimated_tokens > row["token_limit"]:
                    raise QuotaExceeded("Verified token quota would be exceeded")
                self._db.execute("INSERT INTO quota_reservations VALUES (?,?,?,?,?,?)", (reservation_id, provider_id, model_id, 1, estimated_tokens, now.isoformat()))
                self._db.execute(
                    "UPDATE quota_states SET reserved_requests=reserved_requests+1,reserved_tokens=reserved_tokens+? WHERE provider_id=? AND model_id=?",
                    (estimated_tokens, provider_id, model_id),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return reservation_id

    def finalize(self, reservation_id: str, *, actual_tokens: int | None, headers: Mapping[str, str] | None = None) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                reservation = self._db.execute("SELECT * FROM quota_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if not reservation:
                    self._db.execute("COMMIT")
                    return
                charged = reservation["reserved_tokens"] if actual_tokens is None else max(0, actual_tokens)
                limits = {k.lower(): v for k, v in (headers or {}).items() if k.lower().startswith("x-ratelimit-")}
                current = self._db.execute(
                    "SELECT known_limits_json FROM quota_states WHERE provider_id=? AND model_id=?",
                    (reservation["provider_id"], reservation["model_id"]),
                ).fetchone()
                known = json.loads(current["known_limits_json"])
                known.update(limits)
                self._db.execute(
                    """UPDATE quota_states SET requests_used=requests_used+?,tokens_used=tokens_used+?,
                    reserved_requests=reserved_requests-?,reserved_tokens=reserved_tokens-?,last_called_at=?,known_limits_json=?
                    WHERE provider_id=? AND model_id=?""",
                    (reservation["reserved_requests"], charged, reservation["reserved_requests"], reservation["reserved_tokens"],
                     _iso(_utcnow()), json.dumps(known), reservation["provider_id"], reservation["model_id"]),
                )
                self._db.execute("DELETE FROM quota_reservations WHERE reservation_id=?", (reservation_id,))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def rate_limited(self, provider_id: str, model_id: str, retry_after: str | None) -> None:
        seconds = 60
        if retry_after:
            try:
                seconds = max(1, int(float(retry_after)))
            except ValueError:
                pass
        with self._lock:
            self._ensure(provider_id, model_id)
            self._db.execute(
                "UPDATE quota_states SET blocked_until=? WHERE provider_id=? AND model_id=?",
                (_iso(_utcnow() + timedelta(seconds=seconds)), provider_id, model_id),
            )

    def close(self) -> None:
        self._db.close()
