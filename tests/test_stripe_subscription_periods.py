from __future__ import annotations

import pytest

from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.stripe_billing import StripeConfig
from aura_music_studio.stripe_billing_hardening import (
    HardenedSubscriptionCheckoutRequest,
    _subscription_price_id,
    validate_subscription_cycle_invoice,
)


def _config() -> StripeConfig:
    return StripeConfig(
        secret_key="sk_test_only",
        webhook_secret="whsec_test_only",
        public_base_url="https://example.test",
        base_price_id="price_member_monthly",
        pro_price_id="price_pro_monthly",
        settlement_label="test",
    )


def _annual_pro_binding() -> dict:
    return {
        "user_id": "user_pro",
        "plan_id": "pro",
        "billing_period": "annual",
        "stripe_customer_id": "cus_bound",
        "stripe_subscription_id": "sub_bound",
    }


def _annual_pro_invoice(amount_paid: int = 9900) -> dict:
    return {
        "id": "in_annual",
        "status": "paid",
        "billing_reason": "subscription_cycle",
        "currency": "gbp",
        "amount_paid": amount_paid,
        "customer": "cus_bound",
        "subscription": "sub_bound",
    }


def test_checkout_request_defaults_to_monthly_and_accepts_annual():
    monthly = HardenedSubscriptionCheckoutRequest(plan_id="pro")
    annual = HardenedSubscriptionCheckoutRequest(plan_id="pro", billing_period="annual")
    assert monthly.billing_period is BillingPeriod.MONTHLY
    assert annual.billing_period is BillingPeriod.ANNUAL


def test_annual_pro_uses_dedicated_stripe_price_id(monkeypatch):
    monkeypatch.setenv("STRIPE_PRO_ANNUAL_PRICE_ID", "price_pro_annual")
    assert _subscription_price_id(_config(), "pro", BillingPeriod.MONTHLY) == "price_pro_monthly"
    assert _subscription_price_id(_config(), "pro", BillingPeriod.ANNUAL) == "price_pro_annual"


def test_annual_checkout_fails_closed_without_dedicated_price(monkeypatch):
    monkeypatch.delenv("STRIPE_PRO_ANNUAL_PRICE_ID", raising=False)
    with pytest.raises(ValueError, match="annual price id is not configured"):
        _subscription_price_id(_config(), "pro", BillingPeriod.ANNUAL)
    with pytest.raises(ValueError, match="Annual billing is not available"):
        _subscription_price_id(_config(), "base", BillingPeriod.ANNUAL)


def test_annual_pro_renewal_requires_exact_9900_gbp_invoice():
    validate_subscription_cycle_invoice(_annual_pro_invoice(), _annual_pro_binding())


@pytest.mark.parametrize("amount_paid", [999, 9899, 9901])
def test_annual_pro_renewal_rejects_monthly_or_drifted_amount(amount_paid):
    with pytest.raises(ValueError, match="paid amount"):
        validate_subscription_cycle_invoice(_annual_pro_invoice(amount_paid), _annual_pro_binding())


def test_legacy_binding_without_period_remains_monthly_compatible():
    binding = {
        "user_id": "user_base",
        "plan_id": "base",
        "stripe_customer_id": "cus_bound",
        "stripe_subscription_id": "sub_bound",
    }
    invoice = {
        "id": "in_monthly",
        "status": "paid",
        "billing_reason": "subscription_cycle",
        "currency": "gbp",
        "amount_paid": 499,
        "customer": "cus_bound",
        "subscription": "sub_bound",
    }
    validate_subscription_cycle_invoice(invoice, binding)
