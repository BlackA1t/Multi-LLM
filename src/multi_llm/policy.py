from __future__ import annotations

from .models import PolicyDecision, RouteRecord


class ZeroPaidPolicy:
    """A route is permitted only if every independently required proof is true."""

    def evaluate(self, route: RouteRecord | None, *, live_flag: bool, quota_available: bool = False) -> PolicyDecision:
        if route is None:
            return PolicyDecision(allowed=False, reasons=["route_not_registered"])
        evidence = {
            "free_tier_evidence_missing_or_expired": route.free_tier_evidence,
            "free_route_evidence_missing_or_expired": route.free_route_evidence,
            "account_state_evidence_missing_or_expired": route.account_state_evidence,
            "spend_protection_evidence_missing_or_expired": route.spend_protection_evidence,
            "model_verification_evidence_missing_or_expired": route.model_verification_evidence,
            "quota_evidence_missing_or_expired": route.quota_evidence,
        }
        reasons = [reason for reason, item in evidence.items() if item is None or not item.current()]
        checks = {
            "route_not_enabled": route.enabled,
            "model_not_confirmed": bool(route.model_id),
            "not_authenticated": route.authenticated,
            "numeric_quota_missing": route.request_limit is not None or route.token_limit is not None,
            "quota_unavailable": quota_available,
            "route_live_permission_missing": route.live_requests_allowed,
            "live_flag_missing": live_flag,
        }
        reasons.extend(reason for reason, passed in checks.items() if not passed)
        return PolicyDecision(allowed=not reasons, reasons=reasons)
