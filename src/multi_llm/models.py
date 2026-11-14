from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResultStatus(StrEnum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    AUTH_ERROR = "auth_error"
    QUOTA_ERROR = "quota_error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    INCOMPLETE = "incomplete"


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    DRY_RUN = "dry_run"


class GenerationRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model_id: str
    model_family_id: str
    model_developer: str


class TokenUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def total_is_consistent(self) -> "TokenUsage":
        known = [v for v in (self.input_tokens, self.output_tokens) if v is not None]
        if self.total_tokens is not None and known and self.total_tokens < sum(known):
            raise ValueError("total_tokens is smaller than known token components")
        return self


class ProviderResult(BaseModel):
    provider_id: str
    requested_model_id: str
    actual_model_id: str | None = None
    model_family_id: str
    model_developer: str
    auth_method: str
    status: ResultStatus
    response_text: str | None = None
    raw_response: dict[str, Any] | None = None
    duration_ms: int = 0
    usage: TokenUsage | None = None
    error: str | None = None
    error_code: str | None = None
    http_status: int | None = None
    rate_limits: dict[str, Any] = Field(default_factory=dict)

    @property
    def model_id(self) -> str:
        """Compatibility alias for the requested model."""
        return self.requested_model_id


class VerificationEvidence(BaseModel):
    source: str = Field(min_length=3)
    checked_at: datetime
    valid_until: datetime | None = None

    def current(self, at: datetime | None = None) -> bool:
        now = at or utcnow()
        return self.valid_until is None or self.valid_until > now


class RouteRecord(BaseModel):
    provider_id: str
    model_id: str
    model_family_id: str
    model_developer: str
    auth_method: str
    connection_method: str = "official_api"
    enabled: bool = False
    live_requests_allowed: bool = False
    free_tier_evidence: VerificationEvidence | None = None
    free_route_evidence: VerificationEvidence | None = None
    account_state_evidence: VerificationEvidence | None = None
    spend_protection_evidence: VerificationEvidence | None = None
    model_verification_evidence: VerificationEvidence | None = None
    quota_evidence: VerificationEvidence | None = None
    authenticated: bool = False
    request_limit: int | None = Field(default=None, gt=0)
    token_limit: int | None = Field(default=None, gt=0)
    quota_reset_at: datetime | None = None
    estimated_tokens_per_request: int = Field(default=1, gt=0)
    free_tier_notes: str | None = None


class PolicyDecision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=utcnow)
    prompt: str
    participants: list[str]
    results: list[ProviderResult]
    status: RunStatus
    duration_ms: int
