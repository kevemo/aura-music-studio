from __future__ import annotations

from fastapi import HTTPException
import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.membership import MembershipService
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.paypal_webhooks import PayPalWebhookEvidenceStore
from aura_music_studio.plans import get_plan
from aura_music_studio.subscriptions import SubscriptionLedger
import aura_music_studio.membership_api as membership_api


class _NoopAudit:
    def append(self, **kwargs):
        return kwargs


def _install_isolated_membership_api(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    monkeypatch.setattr(membership_api, "store", store)
    monkeypatch.setattr(membership_api, "memberships", MembershipService(store))
    monkeypatch.setattr(membership_api, "subscriptions", SubscriptionLedger(store))
    monkeypatch.setattr(membership_api, "billing_preferences", preferences)
    monkeypatch.setattr(membership_api, "paypal_events", PayPalWebhookEvidenceStore(store))
    monkeypatch.setattr(membership_api, "audit", _NoopAudit())
    monkeypatch.setattr(
        membership_api,
        "notify_membership_request",
        lambda **kwargs: {"delivered": False, "test": True},
    )
    monkeypatch.setattr(
        membership_api,
        "notify_membership_decision",
        lambda **kwargs: {"delivered": False, "test": True},
    )
    monkeypatch.setenv("LSS_ADMIN_KEY", "period-test-admin")
    return store, preferences


def _approve_annual_pro(store, preferences, email="annual@example.com"):
    result = store.signup(email, "Annual Pro", "verysecurepassword", "pro")
    preferences.record_request(
        user_id=result.user_id,
        membership_request_id=result.membership_request_id,
        plan_id="pro",
        billing_period=BillingPeriod.ANNUAL,
    )
    store.decide_membership(result.approval_token, "approve", "Kev")
    preferences.decide(result.membership_request_id, approved=True)
    return result


def _invoice_event(event_id: str, email: str, amount: str) -> dict:
    return {
        "id": event_id,
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": {
            "id": f"INV-{event_id}",
            "status": "PAID",
            "amount": {"currency_code": "GBP", "value": amount},
            "payer_email": email,
        },
    }


def test_canonical_three_tier_catalogue_and_periods_do_not_drift():
    free = get_plan("free")
    basic = get_plan("base")
    pro = get_plan("pro")

    assert free.name == "Free"
    assert str(free.price_for(BillingPeriod.MONTHLY)) == "0.00"
    assert basic.name == "Basic"
    assert str(basic.price_for(BillingPeriod.MONTHLY)) == "4.99"
    with pytest.raises(ValueError, match="Annual billing is not available"):
        basic.price_for(BillingPeriod.ANNUAL)
    assert pro.name == "Unlimited Pro"
    assert str(pro.price_for(BillingPeriod.MONTHLY)) == "9.99"
    assert str(pro.price_for(BillingPeriod.ANNUAL)) == "99.00"


def test_signup_records_annual_pro_period_and_rejects_basic_annual(monkeypatch, tmp_path):
    store, preferences = _install_isolated_membership_api(monkeypatch, tmp_path)

    pro_response = membership_api.signup(
        membership_api.SignupRequest(
            email="signup-annual@example.com",
            display_name="Annual Signup",
            password="verysecurepassword",
            plan_id="pro",
            billing_period=BillingPeriod.ANNUAL,
        )
    )
    assert pro_response["requested_plan"] == "pro"
    assert pro_response["requested_billing_period"] == "annual"
    pro_user = store.get_user_by_email("signup-annual@example.com")
    pro_preference = preferences.for_user(pro_user["id"])
    assert pro_preference is not None
    assert pro_preference["billing_period"] == "annual"
    assert pro_preference["status"] == "requested"

    with pytest.raises(HTTPException) as exc_info:
        membership_api.signup(
            membership_api.SignupRequest(
                email="basic-annual@example.com",
                display_name="Basic Annual",
                password="verysecurepassword",
                plan_id="base",
                billing_period=BillingPeriod.ANNUAL,
            )
        )
    assert exc_info.value.status_code == 400
    assert "Annual billing is not available" in str(exc_info.value.detail)
    assert store.get_user_by_email("basic-annual@example.com") is None


def test_legacy_approved_membership_without_preference_defaults_monthly(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    result = store.signup("legacy-monthly@example.com", "Legacy Monthly", "verysecurepassword", "pro")
    store.decide_membership(result.approval_token, "approve", "Kev")
    assert preferences.approved_period_for_user(result.user_id, "pro") is BillingPeriod.MONTHLY


def test_annual_approval_rejects_monthly_paypal_amount_and_accepts_exact_annual(monkeypatch, tmp_path):
    store, preferences = _install_isolated_membership_api(monkeypatch, tmp_path)
    result = _approve_annual_pro(store, preferences)

    monthly_event = _invoice_event("WH-MONTHLY-WRONG", result.email, "9.99")
    membership_api.paypal_events.record(monthly_event, "TX-WRONG")
    with pytest.raises(HTTPException) as exc_info:
        membership_api.activate_paypal_event(
            membership_api.PayPalEventActivationRequest(
                user_id=result.user_id,
                plan_id="pro",
                event_id="WH-MONTHLY-WRONG",
            ),
            x_lss_admin_key="period-test-admin",
        )
    assert exc_info.value.status_code == 400
    assert "amount does not match" in str(exc_info.value.detail)
    assert store.get_user(result.user_id)["status"] == "approved_pending_payment"

    annual_event = _invoice_event("WH-ANNUAL-CORRECT", result.email, "99.00")
    membership_api.paypal_events.record(annual_event, "TX-CORRECT")
    activated = membership_api.activate_paypal_event(
        membership_api.PayPalEventActivationRequest(
            user_id=result.user_id,
            plan_id="pro",
            event_id="WH-ANNUAL-CORRECT",
        ),
        x_lss_admin_key="period-test-admin",
    )
    assert activated["activated"] is True
    assert activated["plan_id"] == "pro"
    assert activated["billing_period"] == "annual"
    state = membership_api.subscriptions.get(result.user_id)
    assert state is not None
    assert state["billing_period"] == "annual"


def test_basic_annual_preference_is_rejected_before_payment_activation(monkeypatch, tmp_path):
    store, preferences = _install_isolated_membership_api(monkeypatch, tmp_path)
    result = store.signup("basic-paid@example.com", "Basic Paid", "verysecurepassword", "base")
    with pytest.raises(ValueError, match="Annual billing is not available"):
        preferences.record_request(
            user_id=result.user_id,
            membership_request_id=result.membership_request_id,
            plan_id="base",
            billing_period=BillingPeriod.ANNUAL,
        )


def test_plans_contract_no_longer_claims_fixed_31_day_period():
    payload = membership_api.plans()
    assert payload["paid_billing_period_days"] is None
    assert payload["paid_billing_periods"] == {
        "monthly": "calendar_month",
        "annual": "calendar_year",
    }
