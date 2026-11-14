from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from .adapters.base import ProviderAdapter
from .errors import AuthenticationError, ModelMismatch, PolicyDenied, ProviderResponseError, QuotaExceeded
from .models import GenerationRequest, ProviderResult, ResultStatus, RouteRecord, RunRecord, RunStatus
from .policy import ZeroPaidPolicy
from .quota import QuotaManager
from .redaction import redact
from .storage import Storage


class RunManager:
    def __init__(self, adapters: dict[str, ProviderAdapter], policy: ZeroPaidPolicy, quota: QuotaManager, storage: Storage):
        self.adapters, self.policy, self.quota, self.storage = adapters, policy, quota, storage

    async def _one(self, prompt: str, route: RouteRecord, *, live_flag: bool) -> ProviderResult:
        quota_available = self.quota.available(route.provider_id, route.model_id, estimated_tokens=route.estimated_tokens_per_request)
        decision = self.policy.evaluate(route, live_flag=live_flag, quota_available=quota_available)
        if not decision.allowed:
            raise PolicyDenied(",".join(decision.reasons))
        adapter = self.adapters[route.provider_id]
        started = time.perf_counter()
        reservation_id: str | None = None
        result: ProviderResult | None = None
        try:
            reservation_id = self.quota.reserve(route.provider_id, route.model_id, estimated_tokens=route.estimated_tokens_per_request)
            result = await adapter.generate(GenerationRequest(
                prompt=prompt, model_id=route.model_id, model_family_id=route.model_family_id,
                model_developer=route.model_developer,
            ))
            return result
        except PolicyDenied:
            raise
        except AuthenticationError as exc:
            status = ResultStatus.AUTH_ERROR
        except QuotaExceeded as exc:
            status = ResultStatus.RATE_LIMITED
            self.quota.rate_limited(route.provider_id, route.model_id, exc.retry_after)
        except ModelMismatch as exc:
            status = ResultStatus.INVALID_RESPONSE
        except httpx.TimeoutException as exc:
            status = ResultStatus.TIMEOUT
        except (ProviderResponseError, httpx.HTTPError) as exc:
            status = ResultStatus.PROVIDER_ERROR
        except (ValueError, KeyError, TypeError) as exc:
            status = ResultStatus.INVALID_RESPONSE
        finally:
            if reservation_id:
                total = result.usage.total_tokens if result and result.usage else None
                self.quota.finalize(reservation_id, actual_tokens=total, headers=result.rate_limits if result else None)
        return ProviderResult(
            provider_id=route.provider_id, requested_model_id=route.model_id, model_family_id=route.model_family_id,
            model_developer=route.model_developer, auth_method=route.auth_method, status=status,
            duration_ms=int((time.perf_counter() - started) * 1000), error=redact(exc),
            http_status=getattr(exc, "status_code", None), error_code=getattr(exc, "code", None),
        )

    async def run(self, prompt: str, routes: list[RouteRecord], *, live_flag: bool) -> RunRecord:
        if len(routes) != 3 or len({r.model_family_id for r in routes}) != 3:
            await asyncio.gather(*(adapter.aclose() for adapter in self.adapters.values()), return_exceptions=True)
            raise ValueError("A run requires exactly three distinct model families")
        for route in routes:
            self.quota.configure(route.provider_id, route.model_id, request_limit=route.request_limit, token_limit=route.token_limit, reset_at=route.quota_reset_at)
        started = time.perf_counter()
        try:
            gathered = await asyncio.gather(*(self._one(prompt, r, live_flag=live_flag) for r in routes), return_exceptions=True)
        finally:
            await asyncio.gather(*(adapter.aclose() for adapter in self.adapters.values()), return_exceptions=True)
        results: list[ProviderResult] = []
        for route, item in zip(routes, gathered, strict=True):
            if isinstance(item, Exception):
                results.append(ProviderResult(
                    provider_id=route.provider_id, requested_model_id=route.model_id, model_family_id=route.model_family_id,
                    model_developer=route.model_developer, auth_method=route.auth_method,
                    status=ResultStatus.BLOCKED if isinstance(item, PolicyDenied) else ResultStatus.PROVIDER_ERROR,
                    error=redact(item),
                ))
            else:
                results.append(item)
        successes = sum(r.status == ResultStatus.SUCCESS for r in results)
        status = RunStatus.SUCCESS if successes == 3 else RunStatus.PARTIAL if successes else RunStatus.FAILED
        record = RunRecord(
            run_id=str(uuid.uuid4()), prompt=prompt, participants=[r.provider_id for r in routes], results=results,
            status=status, duration_ms=int((time.perf_counter() - started) * 1000),
        )
        self.storage.save_run(record)
        return record

    def dry_run(self, prompt: str, routes: list[RouteRecord]) -> RunRecord:
        results = []
        for route in routes:
            self.quota.configure(route.provider_id, route.model_id, request_limit=route.request_limit, token_limit=route.token_limit, reset_at=route.quota_reset_at)
            decision = self.policy.evaluate(
                route, live_flag=False,
                quota_available=self.quota.available(route.provider_id, route.model_id, estimated_tokens=route.estimated_tokens_per_request),
            )
            results.append(ProviderResult(
                provider_id=route.provider_id, requested_model_id=route.model_id, model_family_id=route.model_family_id,
                model_developer=route.model_developer, auth_method=route.auth_method, status=ResultStatus.BLOCKED,
                error=",".join(decision.reasons),
            ))
        record = RunRecord(run_id=str(uuid.uuid4()), prompt=prompt, participants=[r.provider_id for r in routes], results=results, status=RunStatus.DRY_RUN, duration_ms=0)
        self.storage.save_run(record)
        return record
