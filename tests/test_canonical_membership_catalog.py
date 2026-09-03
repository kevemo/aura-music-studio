from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI

from aura_music_studio.creative_version_autopromotion import router as overlay_router
from aura_music_studio.membership_billing_periods import ensure_billing_period_schema
from aura_music_studio.plans import (
    BILLING_MONTH,
    BILLING_YEAR,
    UNLIMITED_PRO_ANNUAL_GBP,
    UNLIMITED_PRO_MONTHLY_GBP,
    get_plan,
    public_plans,
)
from aura_music_studio.stripe_billing import StripeConfig
from aura_music_studio.stripe_billing_hardening import validate_subscription_cycle_invoice
from aura_music_studio.stripe_membership_periods import (
    CanonicalSubscriptionCheckoutRequest,
    _checkout_payload,
    stripe_membership_price_id,
)


def test_unlimited_pro_uses_owner_approved_canonical_prices():
    pro = get_plan("pro")
    assert pro.monthly_price == UNLIMITED_PRO_MONTHLY_GBP
    assert pro.annual_price == UNLIMITED_PRO_ANNUAL_GBP
    assert pro.price_minor(BILLING_MONTH) == 999
    assert pro.price_minor(BILLING_YEAR) == 9900
    assert pro.supports_billing_period(BILLING_MONTH) is True
    assert pro.supports_billing_period(BILLING_YEAR) is True


def test_public_plan_catalog_exposes_same_monthly_and_annual_source():
    public = {item["id"]: item for item in public_plans()}
    pro = public["pro"]
    assert pro["monthly_price"] == "9.99"
    assert pro["annual_price"] == "99.00"
    assert pro["monthly_price_minor"] == 999
    assert pro["annual_price_minor"] == 9900
    assert pro["billing_options"] == [
        {
            "period": "month",
            "price": "9.99",
            "price_minor": 999,
            "currency": "GBP",
            "display_price": "£9.99",
        },
        {
            "period": "year",
            "price": "99.00",
            "price_minor": 9900,
            "currency": "GBP",
            "display_price": "£99.00",
        },
    ]


def test_tier_two_does_not_invent_an_unapproved_annual_price():
    base = get_plan("base")
    assert base.supports_billing_period(BILLING_MONTH) is True
    assert base.supports_billing_period(BILLING_YEAR) is False
    with pytest.raises(ValueError, match="does not have an annual billing option"):
        base.price(BILLING_YEAR)


def _config() -> StripeConfig:
    return StripeConfig(
        secret_key="sk_test_only",
        webhook_secret="whsec_test_only",
        public_base_url="https://example.test",
        base_price_id="price_base_month",
        pro_price_id="price_pro_month",
        settlement_label="test",
    )


def test_stripe_price_selection_is_period_explicit_and_annual_fails_closed(monkeypatch):
    config = _config()
    assert stripe_membership_price_id(config, "pro", BILLING_MONTH) == "price_pro_month"
    monkeypatch.delenv("STRIPE_PRO_ANNUAL_PRICE_ID", raising=False)
    with pytest.raises(ValueError, match="annual price id is not configured"):
        stripe_membership_price_id(config, "pro", BILLING_YEAR)
    monkeypatch.setenv("STRIPE_PRO_ANNUAL_PRICE_ID", "price_pro_year")
    assert stripe_membership_price_id(config, "pro", BILLING_YEAR) == "price_pro_year"


def test_checkout_payload_carries_period_into_session_and_subscription_metadata(monkeypatch):
    monkeypatch.setenv("STRIPE_PRO_ANNUAL_PRICE_ID", "price_pro_year")
    payload = _checkout_payload(
        _config(),
        {"id": "user_test", "email": "member@example.test"},
        "pro",
        BILLING_YEAR,
    )
    assert payload["line_items[0][price]"] == "price_pro_year"
    assert payload["metadata[plan_id]"] == "pro"
    assert payload["metadata[billing_period]"] == "year"
    assert payload["subscription_data[metadata][plan_id]"] == "pro"
    assert payload["subscription_data[metadata][billing_period]"] == "year"


def test_period_aware_checkout_route_precedes_legacy_subscription_route():
    app = FastAPI()
    app.include_router(overlay_router)
    matches = [route for route in app.router.routes if getattr(route, "path", None) == "/billing/stripe/checkout/subscription"]
    assert len(matches) >= 2
    assert matches[0].endpoint.__module__ == "aura_music_studio.stripe_membership_periods"


def test_checkout_request_defaults_existing_clients_to_monthly():
    request = CanonicalSubscriptionCheckoutRequest(plan_id="pro")
    assert request.billing_period == BILLING_MONTH


def _annual_binding() -> dict:
    return {
        "user_id": "user_test",
        "plan_id": "pro",
        "billing_period": "year",
        "stripe_customer_id": "cus_bound",
        "stripe_subscription_id": "sub_bound",
    }


def _annual_invoice(amount_paid: int = 9900) -> dict:
    return {
        "id": "in_annual",
        "status": "paid",
        "billing_reason": "subscription_cycle",
        "currency": "gbp",
        "amount_paid": amount_paid,
        "customer": "cus_bound",
        "subscription": "sub_bound",
    }


def test_annual_renewal_requires_exact_canonical_annual_amount():
    validate_subscription_cycle_invoice(_annual_invoice(), _annual_binding())
    with pytest.raises(ValueError, match="price and period"):
        validate_subscription_cycle_invoice(_annual_invoice(999), _annual_binding())


def test_billing_period_schema_backfills_existing_rows_as_monthly(tmp_path: Path):
    db = tmp_path / "billing.sqlite3"
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            CREATE TABLE subscription_state (user_id TEXT PRIMARY KEY);
            CREATE TABLE subscription_payments (id TEXT PRIMARY KEY);
            CREATE TABLE stripe_customer_bindings (user_id TEXT PRIMARY KEY);
            INSERT INTO subscription_state(user_id) VALUES ('u1');
            INSERT INTO subscription_payments(id) VALUES ('p1');
            INSERT INTO stripe_customer_bindings(user_id) VALUES ('u1');
            """
        )
        con.commit()
    finally:
        con.close()

    ensure_billing_period_schema(db)

    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT billing_period FROM subscription_state WHERE user_id='u1'").fetchone()[0] == "month"
        assert con.execute("SELECT billing_period FROM subscription_payments WHERE id='p1'").fetchone()[0] == "month"
        assert con.execute("SELECT billing_period FROM stripe_customer_bindings WHERE user_id='u1'").fetchone()[0] == "month"
    finally:
        con.close()
