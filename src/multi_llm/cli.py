from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import webbrowser
from pathlib import Path

from .adapters import GeminiAdapter, GroqAdapter, OpenAISiwcAdapter
from .auth import OpenAIAuthManager, OpenAITokenProvider
from .auth.session import REGISTRATION_CREDENTIAL, TOKEN_CREDENTIAL
from .errors import AuthenticationError, CredentialStoreError
from .config import AppConfig, ConfigStore
from .models import RouteRecord, VerificationEvidence, utcnow
from .policy import ZeroPaidPolicy
from .quota import QuotaManager
from .registry import ProviderRegistry
from .run_manager import RunManager
from .secrets import CredentialStore
from .storage import Storage


def paths(root: Path) -> tuple[ConfigStore, Storage]:
    return ConfigStore(root / "config" / "local.json"), Storage(root / "data" / "runs.db")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multi-llm", description="P0.4 deny-by-default provider prototype")
    p.add_argument("--root", type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("providers", help="Show configured provider routes without network access")
    sub.add_parser("status", help="Validate local configuration without network access")
    key = sub.add_parser("set-key", help="Store an API key in Windows Credential Manager")
    key.add_argument("provider", choices=["gemini", "groq"])
    sub.add_parser("auth-chatgpt", help="Authorize official ChatGPT plan usage with OAuth/OIDC + PKCE")
    route = sub.add_parser("set-route", help="Create/update a route; every safety assertion is explicit")
    route.add_argument("provider", choices=["openai_siwc", "gemini", "groq"])
    route.add_argument("model")
    route.add_argument("--family", required=True)
    route.add_argument("--developer", required=True)
    route.add_argument("--enable", action="store_true")
    route.add_argument("--allow-live", action="store_true")
    route.add_argument("--free-tier-source")
    route.add_argument("--free-route-source")
    route.add_argument("--account-state-source")
    route.add_argument("--spend-protection-source")
    route.add_argument("--model-verification-source")
    route.add_argument("--quota-source")
    route.add_argument("--authenticated", action="store_true")
    route.add_argument("--request-limit", type=int)
    route.add_argument("--token-limit", type=int)
    route.add_argument("--estimated-tokens", type=int, default=1)
    models = sub.add_parser("models", help="Fetch the authenticated provider's official model catalog (not generation)")
    models.add_argument("provider", choices=["openai_siwc", "gemini", "groq"])
    dry = sub.add_parser("dry-run", help="Evaluate a three-route run without provider calls")
    dry.add_argument("prompt")
    run = sub.add_parser("run", help="Perform generation only after all policy proofs and explicit confirmation")
    run.add_argument("prompt")
    run.add_argument("--live", action="store_true", required=True)
    run.add_argument("--confirm-zero-paid", choices=["I_UNDERSTAND"], required=True)
    export = sub.add_parser("export", help="Export a stored run to JSON")
    export.add_argument("run_id")
    export.add_argument("output", type=Path)
    return p


def adapters_for(registry: ProviderRegistry, credentials: CredentialStore, root: Path):
    result = {}
    providers = {route.provider_id for route in registry.list()}
    if "gemini" in providers:
        result["gemini"] = GeminiAdapter(credentials.get("gemini_api_key") or "")
    if "groq" in providers:
        result["groq"] = GroqAdapter(credentials.get("groq_api_key") or "")
    if "openai_siwc" in providers:
        provider = OpenAITokenProvider(credentials, root / "data" / "openai-refresh.lock")
        result["openai_siwc"] = OpenAISiwcAdapter(token_provider=provider)
    return result


def adapter_for_provider(provider_id: str, credentials: CredentialStore, root: Path):
    """Create a discovery adapter independently of generation route configuration."""
    if provider_id == "gemini":
        api_key = credentials.get("gemini_api_key")
        if not api_key:
            raise AuthenticationError("Gemini API key is not configured; run 'multi-llm set-key gemini'")
        return GeminiAdapter(api_key)
    if provider_id == "groq":
        api_key = credentials.get("groq_api_key")
        if not api_key:
            raise AuthenticationError("Groq API key is not configured; run 'multi-llm set-key groq'")
        return GroqAdapter(api_key)
    if provider_id == "openai_siwc":
        if not credentials.get(TOKEN_CREDENTIAL):
            raise AuthenticationError("ChatGPT authorization is missing; run 'multi-llm auth-chatgpt'")
        provider = OpenAITokenProvider(credentials, root / "data" / "openai-refresh.lock")
        return OpenAISiwcAdapter(token_provider=provider)
    raise ValueError(f"Unsupported provider: {provider_id}")


async def async_main(args: argparse.Namespace) -> int:
    config_store, storage = paths(args.root.resolve())
    root = args.root.resolve()
    config = config_store.load()
    registry = ProviderRegistry(config.routes)
    credentials = CredentialStore()
    if args.command == "providers":
        print(json.dumps([r.model_dump(mode="json") for r in registry.list()], ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        quota = QuotaManager(root / "data" / "quota.db")
        decisions = []
        for route in registry.list():
            quota.configure(route.provider_id, route.model_id, request_limit=route.request_limit, token_limit=route.token_limit, reset_at=route.quota_reset_at)
            decisions.append({
                "provider": route.provider_id, "model": route.model_id,
                **ZeroPaidPolicy().evaluate(route, live_flag=False, quota_available=quota.available(
                    route.provider_id, route.model_id, estimated_tokens=route.estimated_tokens_per_request
                )).model_dump(),
            })
        quota.close()
        print(json.dumps({"live_mode_enabled": config.live_mode_enabled, "routes": decisions}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "set-key":
        credentials.set(f"{args.provider}_api_key", getpass.getpass(f"{args.provider} API key: "))
        print("Credential stored in the OS credential store; it was not written to project files.")
        return 0
    if args.command == "auth-chatgpt":
        registration_json = credentials.get(REGISTRATION_CREDENTIAL)
        previous = json.loads(registration_json) if registration_json else {}
        if not config.openai_host_id:
            config.openai_host_id = OpenAIAuthManager.new_host_id()
            config_store.save(config)
        print("Opening the official OpenAI authorization page. No model request will be made.")
        auth = OpenAIAuthManager()
        try:
            tokens = await auth.authorize_loopback(
                host_id=config.openai_host_id, open_browser=webbrowser.open,
                saved_client_id=previous.get("client_id"), id_token_hint=None,
            )
        finally:
            await auth.close()
        token_payload = OpenAIAuthManager.serialize(tokens)
        size = credentials.size_info(token_payload)
        print(
            "OAuth credential size: "
            f"utf8_bytes={size.utf8_bytes}, direct_windows_blob_bytes={size.direct_windows_blob_bytes}, "
            f"chunk_count={size.chunk_count}, max_chunk_windows_blob_bytes={size.max_chunk_windows_blob_bytes}."
        )
        try:
            credentials.set_atomic(TOKEN_CREDENTIAL, token_payload)
            credentials.set_atomic(REGISTRATION_CREDENTIAL, json.dumps({
                "client_id": tokens["client_id"], "identity": tokens.get("identity", {})
            }, separators=(",", ":")))
        except CredentialStoreError as exc:
            print(f"Credential Manager save failed: {exc}", file=sys.stderr)
            return 5
        print("ChatGPT authorization validated; tokens stored in Windows Credential Manager.")
        return 0
    if args.command == "set-route":
        def evidence(source: str | None) -> VerificationEvidence | None:
            return VerificationEvidence(source=source, checked_at=utcnow()) if source else None

        route = RouteRecord(
            provider_id=args.provider, model_id=args.model, model_family_id=args.family,
            model_developer=args.developer, auth_method="oauth_siwc" if args.provider == "openai_siwc" else "api_key",
            enabled=args.enable, live_requests_allowed=args.allow_live,
            free_tier_evidence=evidence(args.free_tier_source), free_route_evidence=evidence(args.free_route_source),
            account_state_evidence=evidence(args.account_state_source), spend_protection_evidence=evidence(args.spend_protection_source),
            model_verification_evidence=evidence(args.model_verification_source), quota_evidence=evidence(args.quota_source),
            authenticated=args.authenticated, request_limit=args.request_limit, token_limit=args.token_limit,
            estimated_tokens_per_request=args.estimated_tokens,
        )
        kept = [r for r in config.routes if (r.provider_id, r.model_id) != (route.provider_id, route.model_id)]
        config.routes = kept + [route]
        config_store.save(config)
        print("Route saved. Missing assertions remain false and will block generation.")
        return 0
    if args.command == "models":
        try:
            adapter = adapter_for_provider(args.provider, credentials, root)
        except AuthenticationError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        try:
            try:
                models = await adapter.get_models()
            except AuthenticationError as exc:
                print(str(exc), file=sys.stderr)
                return 3
            print(json.dumps(models, ensure_ascii=False, indent=2))
        finally:
            await adapter.aclose()
        return 0
    routes = registry.enabled()
    if args.command == "export":
        storage.export(args.run_id, args.output.resolve())
        print(args.output.resolve())
        return 0
    quota = QuotaManager(root / "data" / "quota.db")
    if args.command == "dry-run":
        manager = RunManager({}, ZeroPaidPolicy(), quota, storage)
        record = manager.dry_run(args.prompt, routes)
        print(record.model_dump_json(indent=2))
        quota.close()
        return 0
    if args.command == "run":
        if not config.live_mode_enabled:
            print("Live mode is disabled in local configuration.", file=sys.stderr)
            quota.close()
            return 3
        manager = RunManager(adapters_for(registry, credentials, root), ZeroPaidPolicy(), quota, storage)
        record = await manager.run(args.prompt, routes, live_flag=args.live and args.confirm_zero_paid == "I_UNDERSTAND")
        print(record.model_dump_json(indent=2))
        quota.close()
        return 0 if record.status.value == "success" else 4
    quota.close()
    return 2


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parser().parse_args())))


if __name__ == "__main__":
    main()
