from datetime import datetime, timezone

import pytest

from multi_llm.models import RouteRecord
from multi_llm.quota import QuotaManager
from multi_llm.run_manager import RunManager
from multi_llm.policy import ZeroPaidPolicy
from multi_llm.storage import Storage


def test_retry_after_blocks_route():
    quota = QuotaManager()
    quota.rate_limited("p", "m", "10")
    assert not quota.available("p", "m")
    assert quota.state("p", "m").blocked_until > datetime.now(timezone.utc)


def test_retry_after_invalid_value_uses_bounded_default():
    quota = QuotaManager()
    quota.rate_limited("p", "m", "not-a-number")
    assert not quota.available("p", "m")


async def test_requires_three_distinct_families(tmp_path):
    route = RouteRecord(provider_id="p", model_id="m", model_family_id="same", model_developer="d", auth_method="x")
    manager = RunManager({}, ZeroPaidPolicy(), QuotaManager(), Storage(tmp_path / "db"))
    with pytest.raises(ValueError, match="three distinct"):
        await manager.run("x", [route, route, route], live_flag=True)
