from __future__ import annotations

from aura_music_studio.production_readiness import _stripe_readiness


def _stripe_env() -> dict[str, str]:
    return {
        "STRIPE_SECRET_KEY": "sk_live_annualreadinessfixture_0123456789",
        "STRIPE_WEBHOOK_SECRET": "whsec_annualreadinessfixture_0123456789",
        "STRIPE_BASE_PRICE_ID": "price_basic_monthly_fixture_0123456789",
        "STRIPE_PRO_PRICE_ID": "price_pro_monthly_fixture_0123456789",
        "STRIPE_PRO_ANNUAL_PRICE_ID": "price_pro_annual_fixture_0123456789",
    }


def test_stripe_readiness_requires_pro_annual_price_id():
    env = _stripe_env()
    env.pop("STRIPE_PRO_ANNUAL_PRICE_ID")

    ok, messages, details = _stripe_readiness(env, production=True, staging=False)

    assert ok is False
    assert details["subscription_price_ids_configured"] is False
    assert "STRIPE_PRO_ANNUAL_PRICE_ID" in details["missing_credential_names"]
    assert any("subscription price IDs" in message for message in messages)


def test_stripe_readiness_accepts_complete_monthly_and_pro_annual_configuration():
    env = _stripe_env()

    ok, messages, details = _stripe_readiness(env, production=True, staging=False)

    assert ok is True
    assert messages == []
    assert details["subscription_price_ids_configured"] is True
    assert details["missing_credential_names"] == []
    assert details["browser_return_is_payment_proof"] is False
    assert details["secret_values_exposed"] is False
