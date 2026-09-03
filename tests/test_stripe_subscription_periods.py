from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.stripe_billing import StripeConfig
from aura_music_studio.stripe_billing_hardening import validate_subscription_cycle_invoice
from aura_music_studio.stripe_membership_checkout import (
    MembershipSubscriptionCheckoutRequest,
    approved_checkout_period,
    subscription_price_id,
)
import aura_music_studio.stripe_membership_checkout as stripe_membership_checkout


def _config() -> StripeConfig:
    return StripeConfig(
        secret_key="sk_test_only",
        webhook_secret="whsec_test_only",
        public_base_url="https://example.test",
        base_price_id="price_basic_monthly",
        pro_price_id="price_pro_monthly",
        settlement_label="test",
    )


def _annual_binding(plan_id: str) -> dict:
    return {
        "user_id": f"user_{plan_id}",
        "plan_id": plan_id,
        "billing_period": "annual",
        "stripe_customer_id": "cus_bound",
        "stripe_subscription_id": "sub_bound",
    }


def _annual_invoice(amount_paid: int) -> dict:
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
    monthly = MembershipSubscriptionCheckoutRequest(plan_id="pro")
    annual_basic = MembershipSubscriptionCheckoutRequest(plan_id="base", billing_period="annual")
    annual_pro = MembershipSubscriptionCheckoutRequest(plan_id="pro", billing_period="annual")
    assert monthly.billing_period is BillingPeriod.MONTHLY
    assert annual_basic.billing_period is BillingPeriod.ANNUAL
    assert annual_pro.billing_period is BillingPeriod.ANNUAL


def test_annual_paid_plans_use_dedicated_stripe_price_ids(monkeypatch):
    monkeypatch.setenv("STRIPE_BASE_ANNUAL_PRICE_ID", "price_basic_annual")
    monkeypatch.setenv("STRIPE_PRO_ANNUAL_PRICE_ID", "price_pro_annual")

    assert subscription_price_id(_config(), "base", BillingPeriod.MONTHLY) == "price_basic_monthly"
    assert subscription_price_id(_config(), "base", BillingPeriod.ANNUAL) == "price_basic_annual"
    assert subscription_price_id(_config(), "pro", BillingPeriod.MONTHLY) == "price_pro_monthly"
    assert subscription_price_id(_config(), "pro", BillingPeriod.ANNUAL) == "price_pro_annual"


def test_annual_checkout_fails_closed_without_dedicated_prices(monkeypatch):
    monkeypatch.delenv("STRIPE_BASE_ANNUAL_PRICE_ID", raising=False)
    monkeypatch.delenv("STRIPE_PRO_ANNUAL_PRICE_ID", raising=False)

    with pytest.raises(ValueError, match="annual price id is not configured"):
        subscription_price_id(_config(), "base", BillingPeriod.ANNUAL)
    with pytest.raises(ValueError, match="annual price id is not configured"):
        subscription_price_id(_config(), "pro", BillingPeriod.ANNUAL)


def test_pending_checkout_period_must_match_owner_approval(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    signup = store.signup("annual-checkout@example.com", "Annual Checkout", "verysecurepassword", "pro")
    preferences.record_request(
        user_id=signup.user_id,
        membership_request_id=signup.membership_request_id,
        plan_id="pro",
        billing_period=BillingPeriod.ANNUAL,
    )
    store.decide_membership(signup.approval_token, "approve", "Kev")
    preferences.decide(signup.membership_request_id, approved=True)
    monkeypatch.setattr(stripe_membership_checkout, "billing_preferences", preferences)
    user = store.get_user(signup.user_id)
    assert user is not None

    assert approved_checkout_period(user, "pro", BillingPeriod.ANNUAL) is BillingPeriod.ANNUAL
    with pytest.raises(ValueError, match="does not match the owner-approved"):
        approved_checkout_period(user, "pro", BillingPeriod.MONTHLY)


def test_annual_pro_renewal_requires_exact_9900_gbp_invoice():
    validate_subscription_cycle_invoice(_annual_invoice(9900), _annual_binding("pro"))


def test_annual_basic_renewal_requires_exact_5999_gbp_invoice():
    validate_subscription_cycle_invoice(_annual_invoice(5999), _annual_binding("base"))


@pytest.mark.parametrize("amount_paid", [999, 9899, 9901])
def test_annual_pro_renewal_rejects_monthly_or_drifted_amount(amount_paid):
    with pytest.raises(ValueError, match="paid amount"):
        validate_subscription_cycle_invoice(_annual_invoice(amount_paid), _annual_binding("pro"))


@pytest.mark.parametrize("amount_paid", [599, 5998, 6000])
def test_annual_basic_renewal_rejects_monthly_or_drifted_amount(amount_paid):
    with pytest.raises(ValueError, match="paid amount"):
        validate_subscription_cycle_invoice(_annual_invoice(amount_paid), _annual_binding("base"))


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
        "amount_paid": 599,
        "customer": "cus_bound",
        "subscription": "sub_bound",
    }
    validate_subscription_cycle_invoice(invoice, binding)
