from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Header, Response
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["Operations"])
_STARTED = time.monotonic()

_FALSE = {"", "0", "false", "no", "off", "disabled"}
_PLACEHOLDER_FRAGMENTS = (
    "changeme",
    "change-me",
    "placeholder",
    "example-secret",
    "not-a-production-secret",
    "replace-me",
    "your-secret",
)


def _value(env: Mapping[str, str], name: str, default: str = "") -> str:
    return str(env.get(name, default) or "").strip()


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = _value(env, name, "true" if default else "false").lower()
    return raw not in _FALSE


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_value(env, name, str(default)))
    except ValueError:
        return default


def _secret_configured(env: Mapping[str, str], name: str) -> bool:
    value = _value(env, name)
    lowered = value.lower()
    return bool(value) and len(value) >= 12 and not any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS)


def _secret_group(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[bool, list[str]]:
    missing = [name for name in names if not _secret_configured(env, name)]
    return not missing, missing


def _category(*, ok: bool, required: bool, messages: list[str], details: dict | None = None) -> dict:
    return {
        "ok": bool(ok),
        "required": bool(required),
        "messages": messages,
        "details": details or {},
    }


def _csv_names(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    rows = []
    for item in _value(env, name).split(","):
        item = item.strip()
        if item and item.replace("_", "").isalnum() and item.upper() == item:
            rows.append(item)
    return tuple(dict.fromkeys(rows))


def _storage_details(env: Mapping[str, str]) -> tuple[bool, dict, list[str]]:
    db = Path(_value(env, "LSS_DB_PATH", "data/live_sound_studio.sqlite3"))
    projects = Path(_value(env, "AURA_PROJECTS_ROOT", "projects"))
    backups = Path(_value(env, "LSS_BACKUP_DIR", "backups"))
    paths = [str(db), str(projects), str(backups)]
    distinct = len(set(paths)) == len(paths)
    messages: list[str] = []
    if not distinct:
        messages.append("Database, project and backup storage must use distinct paths.")
    return distinct, {
        "database_path_configured": bool(str(db)),
        "project_path_configured": bool(str(projects)),
        "backup_path_configured": bool(str(backups)),
        "paths_distinct": distinct,
    }, messages


def build_readiness_report(environ: Mapping[str, str] | None = None) -> dict:
    """Return a secret-free deployment readiness report.

    This is a configuration and safety gate, not a provider network probe. External PayPal,
    GPU-renderer and provider reachability are checked by deployment probes/workers so an upstream
    outage cannot make a FastAPI request block on third-party I/O.
    """
    env = environ or os.environ
    deployment = _value(env, "AURA_DEPLOYMENT_ENV", "development").lower()
    if deployment not in {"development", "staging", "production"}:
        deployment = "invalid"
    production = deployment == "production"
    staging = deployment == "staging"
    categories: dict[str, dict] = {}

    # Payments: production uses PayPal's live verification API. Browser returns/manual links are
    # never proof of payment and do not make this gate ready by themselves.
    provider = _value(env, "LSS_PAYMENT_PROVIDER", "paypal").lower()
    payment_mode = _value(env, "LSS_PAYMENT_MODE", "manual_invoice_link").lower()
    paypal_environment = _value(env, "LSS_PAYPAL_ENVIRONMENT", "sandbox" if staging else "live").lower()
    paypal_names = ("LSS_PAYPAL_CLIENT_ID", "LSS_PAYPAL_CLIENT_SECRET", "LSS_PAYPAL_WEBHOOK_ID")
    paypal_creds_ok, paypal_missing = _secret_group(env, paypal_names)
    verified_mode = payment_mode in {"verified_paypal_invoice", "verified_paypal_webhook"}
    payment_messages: list[str] = []
    if provider != "paypal":
        payment_messages.append("The production billing implementation currently supports PayPal only.")
    if production and paypal_environment != "live":
        payment_messages.append("Production PayPal must use the live environment.")
    if staging and paypal_environment != "sandbox":
        payment_messages.append("Staging must use PayPal sandbox credentials; live payment credentials are blocked.")
    if (production or verified_mode or any(_value(env, name) for name in paypal_names)) and not paypal_creds_ok:
        payment_messages.append("Verified PayPal credentials are incomplete or placeholder values.")
    if production and not verified_mode:
        payment_messages.append("Production payment mode must require verified PayPal webhook evidence.")
    payment_ok = (
        provider == "paypal"
        and (not production or (paypal_environment == "live" and paypal_creds_ok and verified_mode))
        and (not staging or paypal_environment == "sandbox")
    )
    categories["payments"] = _category(
        ok=payment_ok,
        required=production or staging,
        messages=payment_messages,
        details={
            "provider": provider,
            "mode": payment_mode,
            "paypal_environment": paypal_environment,
            "verification_credentials_configured": paypal_creds_ok,
            "missing_credential_names": paypal_missing,
            "browser_return_is_payment_proof": False,
            "automatic_activation": False,
        },
    )

    # Provider credentials are named by the operator, not returned. This lets a deployment require
    # only the providers it has actually enabled while still failing closed on missing aliases.
    required_provider_secrets = _csv_names(env, "AURA_PRODUCTION_REQUIRED_PROVIDER_SECRETS")
    missing_provider = [name for name in required_provider_secrets if not _secret_configured(env, name)]
    google_any = bool(_value(env, "GOOGLE_OAUTH_CLIENT_ID") or _value(env, "GOOGLE_OAUTH_CLIENT_SECRET"))
    google_complete = bool(_value(env, "GOOGLE_OAUTH_CLIENT_ID")) and _secret_configured(env, "GOOGLE_OAUTH_CLIENT_SECRET")
    provider_messages: list[str] = []
    if missing_provider:
        provider_messages.append("One or more operator-required provider secrets are missing or placeholders.")
    if google_any and not google_complete:
        provider_messages.append("Google OAuth configuration is incomplete.")
    providers_ok = not missing_provider and (not google_any or google_complete)
    categories["provider_credentials"] = _category(
        ok=providers_ok,
        required=production,
        messages=provider_messages,
        details={
            "required_secret_names": list(required_provider_secrets),
            "missing_secret_names": missing_provider,
            "google_oauth_enabled": google_any,
            "google_oauth_complete": google_complete if google_any else False,
            "secret_values_exposed": False,
        },
    )

    gpu_required = _bool(env, "AURA_GPU_REQUIRED", production)
    renderer_required = _bool(env, "AURA_REQUIRE_LIVE_RENDERER", production)
    ace_url = _value(env, "AURA_ACESTEP_API_URL")
    ace_key_ok = _secret_configured(env, "ACESTEP_API_KEY")
    gpu_messages: list[str] = []
    if gpu_required and not renderer_required:
        gpu_messages.append("GPU production requires the live renderer fail-closed switch.")
    if gpu_required and not ace_url:
        gpu_messages.append("ACE-Step renderer URL is not configured.")
    if gpu_required and not ace_key_ok:
        gpu_messages.append("ACE-Step production API key is missing or a placeholder.")
    gpu_ok = not gpu_required or (renderer_required and bool(ace_url) and ace_key_ok)
    categories["gpu"] = _category(
        ok=gpu_ok,
        required=gpu_required,
        messages=gpu_messages,
        details={
            "required": gpu_required,
            "live_renderer_required": renderer_required,
            "renderer_url_configured": bool(ace_url),
            "renderer_api_key_configured": ace_key_ok,
            "minimum_vram_gb": max(1, _int(env, "AURA_GPU_MIN_VRAM_GB", 12)),
        },
    )

    monitoring_enabled = _bool(env, "AURA_MONITORING_ENABLED", False)
    monitoring_token_ok = _secret_configured(env, "AURA_MONITORING_TOKEN")
    monitoring_ok = not production or (monitoring_enabled and monitoring_token_ok)
    monitoring_messages: list[str] = []
    if production and not monitoring_enabled:
        monitoring_messages.append("Production monitoring is disabled.")
    if production and not monitoring_token_ok:
        monitoring_messages.append("Monitoring token is missing or a placeholder.")
    categories["monitoring"] = _category(
        ok=monitoring_ok,
        required=production,
        messages=monitoring_messages,
        details={
            "enabled": monitoring_enabled,
            "token_configured": monitoring_token_ok,
            "metrics_require_authentication": True,
        },
    )

    backup_enabled = _bool(env, "LSS_AUTO_BACKUP_ENABLED", False)
    backup_interval = _int(env, "LSS_AUTO_BACKUP_INTERVAL_HOURS", 24)
    backup_keep = _int(env, "LSS_AUTO_BACKUP_KEEP", 7)
    backup_age = bool(_value(env, "LSS_BACKUP_AGE_RECIPIENT"))
    backup_ok = not production or (
        backup_enabled and 1 <= backup_interval <= 24 and backup_keep >= 7 and backup_age
    )
    backup_messages: list[str] = []
    if production and not backup_enabled:
        backup_messages.append("Automatic backups are disabled.")
    if production and not (1 <= backup_interval <= 24):
        backup_messages.append("Production backup interval must be between 1 and 24 hours.")
    if production and backup_keep < 7:
        backup_messages.append("Production must retain at least seven automatic backups.")
    if production and not backup_age:
        backup_messages.append("Production backups must configure an age encryption recipient.")
    categories["backups"] = _category(
        ok=backup_ok,
        required=production,
        messages=backup_messages,
        details={
            "automatic": backup_enabled,
            "interval_hours": backup_interval,
            "retention_count": backup_keep,
            "encryption_recipient_configured": backup_age,
            "secrets_in_backup": False,
        },
    )

    public_url = _value(env, "LSS_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    parsed_public = urlparse(public_url)
    cookie_secure = _bool(env, "LSS_COOKIE_SECURE", False)
    provenance_ok = _secret_configured(env, "LSS_PROVENANCE_SECRET")
    admin_ok = _secret_configured(env, "LSS_ADMIN_KEY")
    web_http_allowed = _bool(env, "AURA_WEB_ALLOW_HTTP", False)
    security_ok = not production or (
        parsed_public.scheme == "https"
        and cookie_secure
        and provenance_ok
        and admin_ok
        and not web_http_allowed
    )
    security_messages: list[str] = []
    if production and parsed_public.scheme != "https":
        security_messages.append("Production public base URL must use HTTPS.")
    if production and not cookie_secure:
        security_messages.append("Production cookies must be Secure.")
    if production and not provenance_ok:
        security_messages.append("Signed provenance secret is missing or a placeholder.")
    if production and not admin_ok:
        security_messages.append("Owner bootstrap/admin secret is missing or a placeholder.")
    if production and web_http_allowed:
        security_messages.append("Aura web gateway HTTP must remain disabled in production.")
    categories["security"] = _category(
        ok=security_ok,
        required=production,
        messages=security_messages,
        details={
            "public_https": parsed_public.scheme == "https",
            "secure_cookies": cookie_secure,
            "provenance_secret_configured": provenance_ok,
            "owner_secret_configured": admin_ok,
            "aura_web_plain_http_allowed": web_http_allowed,
            "secret_values_exposed": False,
        },
    )

    storage_ok, storage_details, storage_messages = _storage_details(env)
    categories["storage"] = _category(
        ok=storage_ok,
        required=True,
        messages=storage_messages,
        details=storage_details,
    )

    deployment_messages: list[str] = []
    deployment_ok = deployment != "invalid"
    if deployment == "invalid":
        deployment_messages.append("AURA_DEPLOYMENT_ENV must be development, staging or production.")
    categories["deployment"] = _category(
        ok=deployment_ok,
        required=True,
        messages=deployment_messages,
        details={
            "environment": deployment,
            "staging_uses_live_paypal": staging and paypal_environment == "live",
        },
    )

    blocking = [name for name, item in categories.items() if item["required"] and not item["ok"]]
    ready = not blocking
    return {
        "ok": ready,
        "environment": deployment,
        "production_ready": production and ready,
        "blocking_categories": blocking,
        "categories": categories,
        "network_probes_performed": False,
        "secret_values_exposed": False,
    }


def _monitoring_authorized(token: str | None, environ: Mapping[str, str] | None = None) -> tuple[bool, str]:
    env = environ or os.environ
    configured = _value(env, "AURA_MONITORING_TOKEN")
    if not _bool(env, "AURA_MONITORING_ENABLED", False):
        return False, "monitoring_disabled"
    if not _secret_configured(env, "AURA_MONITORING_TOKEN"):
        return False, "monitoring_token_unconfigured"
    supplied = (token or "").strip()
    return bool(supplied and secrets.compare_digest(configured, supplied)), "authorized"


@router.get("/health/live")
def health_live():
    return {
        "ok": True,
        "kind": "liveness",
        "uptime_seconds": round(max(0.0, time.monotonic() - _STARTED), 3),
    }


@router.get("/health/ready")
def health_ready(response: Response):
    report = build_readiness_report()
    response.status_code = 200 if report["ok"] else 503
    return report


@router.get("/internal/metrics", response_class=PlainTextResponse, include_in_schema=False)
def internal_metrics(
    x_aura_monitoring_token: str | None = Header(default=None),
):
    authorized, reason = _monitoring_authorized(x_aura_monitoring_token)
    if not authorized:
        status_code = 503 if reason != "authorized" and reason != "bad_token" else 403
        if reason == "authorized":
            status_code = 200
        if reason not in {"monitoring_disabled", "monitoring_token_unconfigured"}:
            status_code = 403
        return PlainTextResponse("monitoring unavailable\n", status_code=status_code)
    report = build_readiness_report()
    rows = [
        "# HELP aura_process_up Process liveness.",
        "# TYPE aura_process_up gauge",
        "aura_process_up 1",
        "# HELP aura_readiness Deployment readiness by category.",
        "# TYPE aura_readiness gauge",
    ]
    for name, item in sorted(report["categories"].items()):
        rows.append(f'aura_readiness{{category="{name}"}} {1 if item["ok"] else 0}')
    rows.extend(
        [
            "# HELP aura_production_ready Full production readiness gate.",
            "# TYPE aura_production_ready gauge",
            f'aura_production_ready {1 if report["production_ready"] else 0}',
            "# HELP aura_process_uptime_seconds Process uptime in seconds.",
            "# TYPE aura_process_uptime_seconds gauge",
            f"aura_process_uptime_seconds {max(0.0, time.monotonic() - _STARTED):.3f}",
        ]
    )
    return PlainTextResponse("\n".join(rows) + "\n", media_type="text/plain; version=0.0.4")


def main() -> int:
    report = build_readiness_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_readiness_report", "router"]
