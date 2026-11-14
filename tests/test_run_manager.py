import asyncio

from multi_llm.adapters.fake import FakeAdapter
from multi_llm.models import ResultStatus, RouteRecord, RunStatus, VerificationEvidence, utcnow
from multi_llm.policy import ZeroPaidPolicy
from multi_llm.quota import QuotaManager
from multi_llm.run_manager import RunManager
from multi_llm.storage import Storage


def routes():
    result = []
    proof = VerificationEvidence(source="test fixture", checked_at=utcnow())
    for provider, family, developer in [("a", "gpt", "OpenAI"), ("b", "gemini", "Google"), ("c", "qwen", "Alibaba")]:
        result.append(RouteRecord(
            provider_id=provider, model_id=f"{provider}-model", model_family_id=family, model_developer=developer,
            auth_method="fake", enabled=True, live_requests_allowed=True, free_tier_evidence=proof,
            free_route_evidence=proof, account_state_evidence=proof, spend_protection_evidence=proof,
            model_verification_evidence=proof, quota_evidence=proof, authenticated=True,
            request_limit=10, token_limit=100, estimated_tokens_per_request=2,
        ))
    return result


async def test_parallel_run_and_storage(tmp_path):
    adapters = {r.provider_id: FakeAdapter(r.provider_id, r.model_family_id, r.model_developer, delay=0.08) for r in routes()}
    manager = RunManager(adapters, ZeroPaidPolicy(), QuotaManager(), Storage(tmp_path / "runs.db"))
    started = asyncio.get_running_loop().time()
    record = await manager.run("hello", routes(), live_flag=True)
    elapsed = asyncio.get_running_loop().time() - started
    assert record.status == RunStatus.SUCCESS
    assert elapsed < 0.18
    assert all(a.max_active == 1 for a in adapters.values())


async def test_partial_success_is_preserved(tmp_path):
    rs = routes()
    adapters = {r.provider_id: FakeAdapter(r.provider_id, r.model_family_id, r.model_developer) for r in rs}
    adapters["b"] = FakeAdapter("b", "gemini", "Google", status=ResultStatus.RATE_LIMITED)
    record = await RunManager(adapters, ZeroPaidPolicy(), QuotaManager(), Storage(tmp_path / "db.sqlite")).run("x", rs, live_flag=True)
    assert record.status == RunStatus.PARTIAL
    assert sum(r.status == ResultStatus.SUCCESS for r in record.results) == 2


async def test_same_family_from_different_providers_is_rejected_before_calls(tmp_path):
    rs = routes()
    rs[1].model_family_id = rs[0].model_family_id
    adapters = {r.provider_id: FakeAdapter(r.provider_id, r.model_family_id, r.model_developer) for r in rs}
    manager = RunManager(adapters, ZeroPaidPolicy(), QuotaManager(tmp_path / "quota.db"), Storage(tmp_path / "runs.db"))
    import pytest
    with pytest.raises(ValueError, match="three distinct"):
        await manager.run("x", rs, live_flag=True)
    assert all(adapter.max_active == 0 for adapter in adapters.values())


def test_dry_run_never_calls_adapter(tmp_path):
    rs = routes()
    adapter = FakeAdapter("a", "gpt", "OpenAI")
    manager = RunManager({"a": adapter}, ZeroPaidPolicy(), QuotaManager(), Storage(tmp_path / "db.sqlite"))
    record = manager.dry_run("x", rs)
    assert record.status == RunStatus.DRY_RUN
    assert adapter.max_active == 0
