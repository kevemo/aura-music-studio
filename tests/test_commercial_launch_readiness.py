from __future__ import annotations

import json

from aura_music_studio.commercial_launch_readiness import build_stripe_launch_report


def _production_env() -> dict[str, str]:
    return {
        "AURA_DEPLOYMENT_ENV": "production",
        "LSS_PUBLIC_BASE_URL": "https://studio.example.test",
        "STRIPE_SECRET_KEY": "sk_live_fixture_0123456789",
        "STRIPE_WEBHOOK_SECRET": "whsec_fixture_0123456789",
        "STRIPE_BASE_PRICE_ID": "price_basic_fixture_0123456789",
        "STRIPE_PRO_PRICE_ID": "price_pro_month_fixture_0123456789",
        "STRIPE_PRO_ANNUAL_PRICE_ID": "price_pro_year_fixture_0123456789",
    }


def test_complete_production_stripe_launch_contract_passes_without_network_evidence():
    env = _production_env()
    report = build_stripe_launch_report(env)

    assert report["ok"] is True
    assert report["deployment"] == "production"
    assert report["canonical_catalogue_ok"] is True
    assert report["public_base_url_acceptable"] is True
    assert report["configured"]["STRIPE_PRO_ANNUAL_PRICE_ID"] is True
    assert report["production_settlement_proven"] is False
    assert report["network_probes_performed"] is False
    assert report["browser_return_is_payment_proof"] is False
    assert report["secret_values_exposed"] is False

    serialized = json.dumps(report)
    assert env["STRIPE_SECRET_KEY"] not in serialized
    assert env["STRIPE_WEBHOOK_SECRET"] not in serialized


def test_production_fails_closed_when_annual_pro_price_is_missing():
    env = _production_env()
    env.pop("STRIPE_PRO_ANNUAL_PRICE_ID")
    report = build_stripe_launch_report(env)

    assert report["ok"] is False
    assert report["configured"]["STRIPE_PRO_ANNUAL_PRICE_ID"] is False
    assert "STRIPE_PRO_ANNUAL_PRICE_ID" in report["missing_names"]


def test_production_rejects_test_secret_and_non_https_public_url():
    env = _production_env()
    env["STRIPE_SECRET_KEY"] = "sk_test_fixture_0123456789"
    env["LSS_PUBLIC_BASE_URL"] = "http://studio.example.test"
    report = build_stripe_launch_report(env)

    assert report["ok"] is False
    assert report["configured"]["STRIPE_SECRET_KEY"] is False
    assert report["public_base_url_acceptable"] is False


def test_staging_accepts_test_key_but_rejects_live_key():
    env = _production_env()
    env["AURA_DEPLOYMENT_ENV"] = "staging"
    env["STRIPE_SECRET_KEY"] = "sk_test_fixture_0123456789"
    assert build_stripe_launch_report(env)["ok"] is True

    env["STRIPE_SECRET_KEY"] = "sk_live_fixture_0123456789"
    report = build_stripe_launch_report(env)
    assert report["ok"] is False
    assert report["configured"]["STRIPE_SECRET_KEY"] is False
