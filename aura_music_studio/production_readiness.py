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

from .access_control import PUBLIC_EXACT
from .operational_evidence import load_restore_evidence, probe_runtime_storage

router = APIRouter(tags=["Operations"])
_STARTED = time.monotonic()

# These GET-only operational paths must pass the membership envelope so container/orchestrator
# health checks work before any customer signs in. `/internal/metrics` remains independently
# protected by a constant-time monitoring-token comparison below.
_OPERATIONS_PUBLIC_PATHS = {"/health/live", "/health/ready", "/internal/metrics"}
PUBLIC_EXACT.update(_OPERATIONS_PUBLIC_PATHS)

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
_DEPLOYMENT_ENVIRONMENTS = {"local", "development", "test", "ci", "integration", "staging", "production"}


def _value(env: Mapping[str, str], name: str, default: str = "") -> str:
    return str(env.get(name, default) or "").strip()


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    return _value(env, name, "true" if default else "false").lower() not in _FALSE


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_value(env, name, str(default)))
    except ValueError:
        return default


def _secret_configured(env: Mapping[str, str], name: str) -> bool:
    value = _value(env, name)
    lowered = value.lower()
    return bool(value) and len(value) >= 12 and not any(part in lowered for part in _PLACEHOLDER_FRAGMENTS)


def _secret_group(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[bool, list[str]]:
    missing = [name for name in names if not _secret_configured(env, name)]
    return not missing, missing


def _category(*, ok: bool, required: bool, messages: list[str], details: dict | None = None) -> dict:
    return {"ok": bool(ok), "required": bool(required), "messages": messages, "details": details or {}}


def _csv_names(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    rows: list[str] = []
    for raw in _value(env, name).split(","):
        item = raw.strip()
        if item and item.replace("_", "").isalnum() and item.upper() == item:
            rows.append(item)
    return tuple(dict.fromkeys(rows))


def _storage_details(env: Mapping[str, str]) -> tuple[bool, dict, list[str]]:
    db = Path(_value(env, "LSS_DB_PATH", "data/live_sound_studio.sqlite3"))
    projects = Path(_value(env, "AURA_PROJECTS_ROOT", "projects"))
    backups = Path(_value(env, "LSS_BACKUP_DIR", "backups"))
    paths = [str(db), str(projects), str(backups)]
    distinct = len(set(paths)) == len(paths)
    messages = [] if distinct else ["Database, project and backup storage must use distinct paths."]
    return distinct, {
        "database_path_configured": bool(str(db)),
        "project_path_configured": bool(str(projects)),
        "backup_path_configured": bool(str(backups)),
        "paths_distinct": distinct,
    }, messages


def _stripe_readiness(env: Mapping[str, str], *, production: bool, nonproduction: bool) -> tuple[bool, list[str], dict]:
    names = (
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_BASE_PRICE_ID",
        "STRIPE_PRO_PRICE_ID",
        "STRIPE_PRO_ANNUAL_PRICE_ID",
    )
    secret = _value(env, "STRIPE_SECRET_KEY")
    webhook = _value(env, "STRIPE_WEBHOOK_SECRET")
    base_price = _value(env, "STRIPE_BASE_PRICE_ID")
    pro_price = _value(env, "STRIPE_PRO_PRICE_ID")
    pro_annual_price = _value(env, "STRIPE_PRO_ANNUAL_PRICE_ID")

    configured = {
        "STRIPE_SECRET_KEY": _secret_configured(env, "STRIPE_SECRET_KEY") and secret.startswith(("sk_live_", "sk_test_")),
        "STRIPE_WEBHOOK_SECRET": _secret_configured(env, "STRIPE_WEBHOOK_SECRET") and webhook.startswith("whsec_"),
        "STRIPE_BASE_PRICE_ID": _secret_configured(env, "STRIPE_BASE_PRICE_ID") and base_price.startswith("price_"),
        "STRIPE_PRO_PRICE_ID": _secret_configured(env, "STRIPE_PRO_PRICE_ID") and pro_price.startswith("price_"),
        "STRIPE_PRO_ANNUAL_PRICE_ID": _secret_configured(env, "STRIPE_PRO_ANNUAL_PRICE_ID") and pro_annual_price.startswith("price_"),
    }
    missing = [name for name in names if not configured[name]]
    messages: list[str] = []
    if missing:
        messages.append("Stripe credentials or server-authoritative subscription price IDs are incomplete, malformed or placeholders.")
    if production and configured["STRIPE_SECRET_KEY"] and not secret.startswith("sk_live_"):
        messages.append("Production Stripe must use a live secret key.")
    if nonproduction and configured["STRIPE_SECRET_KEY"] and secret.startswith("sk_live_"):
        messages.append("Non-production environments must not use a Stripe live secret key.")

    environment_ok = (not production or secret.startswith("sk_live_")) and (
        not nonproduction or not secret.startswith("sk_live_")
    )
    ok = not missing and environment_ok
    return ok, messages, {
        "provider": "stripe",
        "mode": "signed_stripe_webhook",
        "stripe_environment": "live" if secret.startswith("sk_live_") else "test" if secret.startswith("sk_test_") else "invalid",
        "verification_credentials_configured": configured["STRIPE_WEBHOOK_SECRET"],
        "subscription_price_ids_configured": (
            configured["STRIPE_BASE_PRICE_ID"]
            and configured["STRIPE_PRO_PRICE_ID"]
            and configured["STRIPE_PRO_ANNUAL_PRICE_ID"]
        ),
        "missing_credential_names": missing,
        "browser_return_is_payment_proof": False,
        "automatic_activation": True,
        "secret_values_exposed": False,
    }


def build_readiness_report(
    environ: Mapping[str, str] | None = None,
    *,
    perform_runtime_probes: bool = False,
) -> dict:
    """Build a secret-free readiness report.

    Configuration readiness, runtime dependency readiness and release/restore evidence are kept
    separate. A config-only CI smoke can therefore validate fail-closed settings without ever
    being described as proof that the deployed production system is healthy.
    """
    env = environ or os.environ
    deployment = _value(env, "AURA_DEPLOYMENT_ENV", "development").lower()
    if deployment not in _DEPLOYMENT_ENVIRONMENTS:
        deployment = "invalid"
    production = deployment == "production"
    staging = deployment == "staging"
    nonproduction = deployment in (_DEPLOYMENT_ENVIRONMENTS - {"production"})
    categories: dict[str, dict] = {}

    provider = _value(env, "LSS_PAYMENT_PROVIDER", "paypal").lower()
    payment_mode = _value(env, "LSS_PAYMENT_MODE", "manual_invoice_link").lower()
    paypal_environment = _value(env, "LSS_PAYPAL_ENVIRONMENT", "live" if production else "sandbox").lower()
    paypal_names = ("LSS_PAYPAL_CLIENT_ID", "LSS_PAYPAL_CLIENT_SECRET", "LSS_PAYPAL_WEBHOOK_ID")
    paypal_creds_ok, paypal_missing = _secret_group(env, paypal_names)
    verified_mode = payment_mode in {"verified_paypal_invoice", "verified_paypal_webhook"}

    if provider == "stripe":
        payment_ok, payment_messages, payment_details = _stripe_readiness(
            env,
            production=production,
            nonproduction=nonproduction,
        )
    elif provider == "paypal":
        payment_messages = []
        if production and paypal_environment != "live":
            payment_messages.append("Production PayPal must use the live environment.")
        if nonproduction and paypal_environment == "live":
            payment_messages.append("Non-production environments must use PayPal sandbox credentials.")
        if (production or verified_mode or any(_value(env, name) for name in paypal_names)) and not paypal_creds_ok:
            payment_messages.append("Verified PayPal credentials are incomplete or placeholder values.")
        if production and not verified_mode:
            payment_messages.append("Production payment mode must require verified PayPal webhook evidence.")
        payment_ok = (
            (not verified_mode or paypal_creds_ok)
            and (not production or (paypal_environment == "live" and paypal_creds_ok and verified_mode))
            and (not nonproduction or paypal_environment != "live")
        )
        payment_details = {
            "provider": provider,
            "mode": payment_mode,
            "paypal_environment": paypal_environment,
            "verification_credentials_configured": paypal_creds_ok,
            "missing_credential_names": paypal_missing,
            "browser_return_is_payment_proof": False,
            "automatic_activation": False,
            "secret_values_exposed": False,
        }
    else:
        payment_ok = False
        payment_messages = ["Unsupported payment provider. Configure PayPal or Stripe."]
        payment_details = {
            "provider": provider,
            "mode": payment_mode,
            "verification_credentials_configured": False,
            "missing_credential_names": [],
            "browser_return_is_payment_proof": False,
            "automatic_activation": False,
            "secret_values_exposed": False,
        }

    categories["payments"] = _category(
        ok=payment_ok,
        required=production or staging,
        messages=payment_messages,
        details=payment_details,
    )

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
    monitoring_messages: list[str] = []
    if production and not monitoring_enabled:
        monitoring_messages.append("Production monitoring is disabled.")
    if production and not monitoring_token_ok:
        monitoring_messages.append("Monitoring token is missing or a placeholder.")
    categories["monitoring"] = _category(
        ok=(not production or (monitoring_enabled and monitoring_token_ok)),
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
    backup_ok = not production or (backup_enabled and 1 <= backup_interval <= 24 and backup_keep >= 7 and backup_age)
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
            "configuration_is_restore_proof": False,
        },
    )

    restore_max_age = max(1, _int(env, "LSS_RESTORE_EVIDENCE_MAX_AGE_HOURS", 24 * 30))
    restore = load_restore_evidence(_value(env, "LSS_RESTORE_EVIDENCE_PATH") or None, max_age_hours=restore_max_age)
    restore_messages = [] if restore.get("verified") else [str(restore.get("reason") or "No verified restore-drill evidence is available.")]
    categories["restore_evidence"] = _category(
        ok=bool(restore.get("verified")),
        required=False,
        messages=restore_messages,
        details={**restore, "max_age_hours": restore_max_age, "release_gate_only": True},
    )

    public_url = _value(env, "LSS_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    parsed_public = urlparse(public_url)
    cookie_secure = _bool(env, "LSS_COOKIE_SECURE", False)
    provenance_ok = _secret_configured(env, "LSS_PROVENANCE_SECRET")
    admin_ok = _secret_configured(env, "LSS_ADMIN_KEY")
    web_http_allowed = _bool(env, "AURA_WEB_ALLOW_HTTP", False)
    security_ok = not production or (
        parsed_public.scheme == "https" and cookie_secure and provenance_ok and admin_ok and not web_http_allowed
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
    categories["storage"] = _category(ok=storage_ok, required=True, messages=storage_messages, details=storage_details)

    stripe_secret = _value(env, "STRIPE_SECRET_KEY")
    nonproduction_live_paypal = nonproduction and provider == "paypal" and paypal_environment == "live"
    nonproduction_live_stripe = nonproduction and provider == "stripe" and stripe_secret.startswith("sk_live_")
    deployment_valid = deployment != "invalid"
    deployment_ok = deployment_valid and not nonproduction_live_paypal and not nonproduction_live_stripe
    deployment_messages: list[str] = []
    if not deployment_valid:
        deployment_messages.append(
            "AURA_DEPLOYMENT_ENV must be local, development, test, ci, integration, staging or production."
        )
    if nonproduction_live_paypal or nonproduction_live_stripe:
        deployment_messages.append("Non-production environments are blocked from live payment credentials/endpoints.")
    categories["deployment"] = _category(
        ok=deployment_ok,
        required=True,
        messages=deployment_messages,
        details={
            "environment": deployment,
            "staging_uses_live_paypal": staging and provider == "paypal" and paypal_environment == "live",
            "staging_uses_live_stripe": staging and provider == "stripe" and stripe_secret.startswith("sk_live_"),
            "nonproduction_uses_live_paypal": nonproduction_live_paypal,
            "nonproduction_uses_live_stripe": nonproduction_live_stripe,
        },
    )

    if perform_runtime_probes:
        runtime = probe_runtime_storage(env)
        runtime_ok = bool(runtime.get("verified"))
        runtime_messages = [] if runtime_ok else ["One or more critical local durable-state dependencies are unavailable or degraded."]
    else:
        runtime = {
            "verified": False,
            "state": "unknown",
            "external_provider_probes_performed": False,
            "destructive_writes_performed": False,
        }
        runtime_ok = False
        runtime_messages = ["Runtime dependency probes were not performed for this report."]
    categories["runtime_dependencies"] = _category(
        ok=runtime_ok,
        required=False,
        messages=runtime_messages,
        details=runtime,
    )

    configuration_blocking = [name for name, item in categories.items() if item["required"] and not item["ok"]]
    configuration_ready = not configuration_blocking
    serving_blocking = list(configuration_blocking)
    if perform_runtime_probes and not runtime_ok:
        serving_blocking.append("runtime_dependencies")
    serving_ready = configuration_ready and (runtime_ok if perform_runtime_probes else True)

    release_blocking = list(serving_blocking)
    if production and not perform_runtime_probes:
        release_blocking.append("runtime_dependencies")
    if production and not restore.get("verified"):
        release_blocking.append("restore_evidence")
    release_blocking = list(dict.fromkeys(release_blocking))
    production_ready = bool(production and configuration_ready and runtime_ok and restore.get("verified"))

    return {
        "ok": serving_ready,
        "configuration_ready": configuration_ready,
        "serving_ready": serving_ready,
        "environment": deployment,
        "production_ready": production_ready,
        "blocking_categories": configuration_blocking,
        "serving_blocking_categories": serving_blocking,
        "release_blocking_categories": release_blocking,
        "categories": categories,
        "runtime_probes_performed": perform_runtime_probes,
        "network_probes_performed": False,
        "external_provider_runtime_verified": False,
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
    if not supplied or not secrets.compare_digest(configured, supplied):
        return False, "bad_token"
    return True, "authorized"


@router.get("/health/live")
def health_live():
    return {"ok": True, "kind": "liveness", "uptime_seconds": round(max(0.0, time.monotonic() - _STARTED), 3)}


@router.get("/health/ready")
def health_ready(response: Response):
    report = build_readiness_report(perform_runtime_probes=True)
    response.status_code = 200 if report["serving_ready"] else 503
    return report


@router.get("/internal/metrics", response_class=PlainTextResponse, include_in_schema=False)
def internal_metrics(x_aura_monitoring_token: str | None = Header(default=None)):
    authorized, reason = _monitoring_authorized(x_aura_monitoring_token)
    if not authorized:
        return PlainTextResponse(
            "monitoring unavailable\n",
            status_code=503 if reason in {"monitoring_disabled", "monitoring_token_unconfigured"} else 403,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    report = build_readiness_report(perform_runtime_probes=True)
    rows = [
        "# HELP aura_process_up Process liveness.",
        "# TYPE aura_process_up gauge",
        "aura_process_up 1",
        "# HELP aura_readiness Deployment configuration readiness by category.",
        "# TYPE aura_readiness gauge",
    ]
    for name, item in sorted(report["categories"].items()):
        rows.append(f'aura_readiness{{category="{name}"}} {1 if item["ok"] else 0}')
    rows.extend([
        "# HELP aura_configuration_ready Required deployment configuration is valid.",
        "# TYPE aura_configuration_ready gauge",
        f'aura_configuration_ready {1 if report["configuration_ready"] else 0}',
        "# HELP aura_serving_ready Critical local dependencies are available for serving traffic.",
        "# TYPE aura_serving_ready gauge",
        f'aura_serving_ready {1 if report["serving_ready"] else 0}',
        "# HELP aura_restore_evidence_verified A fresh isolated restore drill has been verified.",
        "# TYPE aura_restore_evidence_verified gauge",
        f'aura_restore_evidence_verified {1 if report["categories"]["restore_evidence"]["ok"] else 0}',
        "# HELP aura_production_ready Strict production release readiness including runtime and restore evidence.",
        "# TYPE aura_production_ready gauge",
        f'aura_production_ready {1 if report["production_ready"] else 0}',
        "# HELP aura_process_uptime_seconds Process uptime in seconds.",
        "# TYPE aura_process_uptime_seconds gauge",
        f"aura_process_uptime_seconds {max(0.0, time.monotonic() - _STARTED):.3f}",
    ])
    return PlainTextResponse(
        "\n".join(rows) + "\n",
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def main() -> int:
    report = build_readiness_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["configuration_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_readiness_report", "router"]
