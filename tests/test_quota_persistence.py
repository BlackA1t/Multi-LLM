import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from multi_llm.errors import QuotaExceeded
from multi_llm.quota import QuotaManager


def test_quota_survives_independent_manager_restart(tmp_path):
    path = tmp_path / "quota.db"
    first = QuotaManager(path)
    reset_at = datetime.now(timezone.utc) + timedelta(hours=1)
    first.configure("p", "m", request_limit=3, token_limit=20, reset_at=reset_at)
    reservation = first.reserve("p", "m", estimated_tokens=5)
    first.finalize(reservation, actual_tokens=4, headers={"x-ratelimit-limit-requests": "3"})
    first.close()

    second = QuotaManager(path)
    state = second.state("p", "m")
    assert state.requests == 1 and state.tokens == 4
    assert state.reset_at == reset_at
    assert state.known_limits["x-ratelimit-limit-requests"] == "3"
    second.close()


def test_retry_after_survives_restart(tmp_path):
    path = tmp_path / "quota.db"
    first = QuotaManager(path)
    first.configure("p", "m", request_limit=3, token_limit=None, reset_at=None)
    first.rate_limited("p", "m", "120")
    first.close()
    second = QuotaManager(path)
    assert not second.available("p", "m")
    with pytest.raises(QuotaExceeded, match="Retry-After"):
        second.reserve("p", "m", estimated_tokens=1)
    second.close()


async def test_concurrent_managers_reserve_atomically(tmp_path):
    path = tmp_path / "quota.db"
    first, second = QuotaManager(path), QuotaManager(path)
    first.configure("p", "m", request_limit=1, token_limit=10, reset_at=None)

    async def reserve(manager):
        try:
            return await asyncio.to_thread(manager.reserve, "p", "m", estimated_tokens=5)
        except QuotaExceeded:
            return None

    results = await asyncio.gather(reserve(first), reserve(second))
    assert sum(result is not None for result in results) == 1
    assert first.state("p", "m").reserved_requests == 1
    first.close()
    second.close()


def test_unknown_numeric_quota_fails_closed(tmp_path):
    quota = QuotaManager(tmp_path / "quota.db")
    assert not quota.available("p", "m")
    with pytest.raises(QuotaExceeded, match="No verified numeric quota"):
        quota.reserve("p", "m", estimated_tokens=1)
    quota.close()
