from __future__ import annotations

import json
import os
from collections.abc import Mapping
from urllib.parse import urlparse

from .native_products import BillingPeriod
from .plans import get_plan

_PLACEHOLDERS = ("changeme", "change-me", "placeholder", "replace-me", "your-secret")


def _value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name, "") or "").strip()


def _valid_secret(value: str, prefix: str) -> bool:
    lowered = value.lower()
    return bool(value) and value.startswith(prefix) and len(value) >= 12 and not any(token in lowered for token in _PLACEHOLDERS)


def _valid_price_id(value: str) -> bool:
    lowered = value.lower()
    return bool(value) and value.startswith("price_") and len(value) >= 12 and not any(token in lowered for token in _PLACEHOLDERS)


def build_stripe_launch_report(environ: Mapping[str, str] | None = None) -> dict:
    """Validate the code-side Stripe subscription launch contract without network I/O.

    This verifies configuration shape only. It deliberately does not claim that Stripe has
    accepted a live payment, emitted a webhook, settled funds, processed a refund, or paid a
    marketplace creator. Those remain production-evidence gates.
    """

    env = environ or os.environ
    deployment = _value(env, "AURA_DEPLOYMENT_ENV").lower() or "development"
    production = deployment == "production"
    staging = deployment == "staging"

    secret = _value(env, "STRIPE_SECRET_KEY")
    webhook = _value(env, "STRIPE_WEBHOOK_SECRET")
    base_monthly = _value(env, "STRIPE_BASE_PRICE_ID")
    pro_monthly = _value(env, "STRIPE_PRO_PRICE_ID")
    pro_annual = _value(env, "STRIPE_PRO_ANNUAL_PRICE_ID")
    public_url = _value(env, "LSS_PUBLIC_BASE_URL")

    configured = {
        "STRIPE_SECRET_KEY": _valid_secret(secret, "sk_live_" if production else "sk_test_" if staging else "sk_"),
        "STRIPE_WEBHOOK_SECRET": _valid_secret(webhook, "whsec_"),
        "STRIPE_BASE_PRICE_ID": _valid_price_id(base_monthly),
        "STRIPE_PRO_PRICE_ID": _valid_price_id(pro_monthly),
        "STRIPE_PRO_ANNUAL_PRICE_ID": _valid_price_id(pro_annual),
    }
    missing = [name for name, ok in configured.items() if not ok]

    parsed = urlparse(public_url)
    public_url_ok = bool(public_url) and (
        parsed.scheme == "https"
        or (not production and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
    )

    basic = get_plan("base")
    pro = get_plan("pro")
    basic_annual_disabled = basic.annual_price is None
    catalogue_ok = (
        basic.name == "Basic"
        and basic.currency == "GBP"
        and str(basic.price_for(BillingPeriod.MONTHLY)) == "4.99"
        and basic_annual_disabled
        and pro.name == "Unlimited Pro"
        and pro.currency == "GBP"
        and str(pro.price_for(BillingPeriod.MONTHLY)) == "9.99"
        and str(pro.price_for(BillingPeriod.ANNUAL)) == "99.00"
    )

    messages: list[str] = []
    if missing:
        messages.append("Stripe subscription credentials or canonical price IDs are incomplete, malformed, or placeholders.")
    if not public_url_ok:
        messages.append("Stripe Checkout requires an allowed public base URL; production must use HTTPS.")
    if not catalogue_ok:
        messages.append("The canonical Free / Basic / Unlimited Pro catalogue has drifted from the approved pricing contract.")

    ok = not missing and public_url_ok and catalogue_ok
    return {
        "ok": ok,
        "deployment": deployment,
        "provider": "stripe",
        "configuration_only": True,
        "network_probes_performed": False,
        "production_settlement_proven": False,
        "browser_return_is_payment_proof": False,
        "configured": configured,
        "missing_names": missing,
        "public_base_url_acceptable": public_url_ok,
        "canonical_catalogue_ok": catalogue_ok,
        "canonical_prices": {
            "basic_monthly_gbp": "4.99",
            "basic_annual_available": False,
            "unlimited_pro_monthly_gbp": "9.99",
            "unlimited_pro_annual_gbp": "99.00",
        },
        "messages": messages,
        "secret_values_exposed": False,
    }


def main() -> int:
    report = build_stripe_launch_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_stripe_launch_report"]
