from multi_llm.models import RouteRecord, VerificationEvidence, utcnow
from multi_llm.policy import ZeroPaidPolicy


def route(**updates):
    proof = VerificationEvidence(source="official account page", checked_at=utcnow())
    values = dict(
        provider_id="p", model_id="m", model_family_id="f", model_developer="d", auth_method="api_key",
        enabled=True, live_requests_allowed=True, free_tier_evidence=proof, free_route_evidence=proof,
        account_state_evidence=proof, spend_protection_evidence=proof, model_verification_evidence=proof,
        quota_evidence=proof, authenticated=True, request_limit=10,
    )
    values.update(updates)
    return RouteRecord(**values)


def test_policy_allows_only_complete_proof():
    assert ZeroPaidPolicy().evaluate(route(), live_flag=True, quota_available=True).allowed


def test_policy_denies_unknown_pricing_and_missing_live_flag():
    decision = ZeroPaidPolicy().evaluate(route(account_state_evidence=None), live_flag=False, quota_available=True)
    assert not decision.allowed
    assert "account_state_evidence_missing_or_expired" in decision.reasons
    assert "live_flag_missing" in decision.reasons


def test_policy_denies_unregistered_route():
    assert ZeroPaidPolicy().evaluate(None, live_flag=True).reasons == ["route_not_registered"]


def test_manual_boolean_like_claims_cannot_replace_evidence():
    unsafe = RouteRecord(provider_id="p", model_id="m", model_family_id="f", model_developer="d", auth_method="api_key", enabled=True, live_requests_allowed=True, authenticated=True, request_limit=10)
    decision = ZeroPaidPolicy().evaluate(unsafe, live_flag=True, quota_available=True)
    assert not decision.allowed
    assert "free_route_evidence_missing_or_expired" in decision.reasons
