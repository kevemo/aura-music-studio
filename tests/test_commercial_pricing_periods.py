from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.billing import payment_option, public_payment_options
from aura_music_studio.plans import BILLING_ANNUAL, BILLING_MONTHLY, get_plan, public_plans
from aura_music_studio.subscriptions import SubscriptionLedger, _advance_period


def _approved_user(store: AccountStore, email: str, plan_id: str = "pro"):
    signup = store.signup(email, "Pricing Test", "very-secure-password", plan_id)
    approved = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert approved["status"] == "approved_pending_payment"
    return signup


def test_unlimited_pro_has_one_canonical_monthly_and_annual_price():
    pro = get_plan("pro")
    assert pro.monthly_price == Decimal("9.99")
    assert pro.annual_price == Decimal("99.00")
    assert pro.price_minor(BILLING_MONTHLY) == 999
    assert pro.price_minor(BILLING_ANNUAL) == 9900

    public = next(item for item in public_plans() if item["id"] == "pro")
    assert public["monthly_price"] == "9.99"
    assert public["annual_price"] == "99.00"
    assert public["monthly_price_minor"] == 999
    assert public["annual_price_minor"] == 9900
    assert public["billing_periods"] == ["monthly", "annual"]


def test_unsupported_or_unknown_billing_periods_fail_closed():
    with pytest.raises(ValueError, match="does not offer annual billing"):
        get_plan("base").price_for_period(BILLING_ANNUAL)
    with pytest.raises(ValueError, match="Unknown billing period"):
        get_plan("pro").price_for_period("weekly")


def test_annual_checkout_requires_a_dedicated_server_route(monkeypatch):
    monkeypatch.delenv("LSS_PAYPAL_PRO_ANNUAL_URL", raising=False)
    with pytest.raises(ValueError, match="Annual Pro payment route is not configured"):
        payment_option("pro", BILLING_ANNUAL)

    monkeypatch.setenv("LSS_PAYPAL_PRO_ANNUAL_URL", "https://example.invalid/pro-annual")
    annual = payment_option("pro", BILLING_ANNUAL)
    assert annual is not None
    assert annual.amount == "99.00"
    assert annual.amount_minor == 9900
    assert annual.billing_period == "annual"

    # Do not advertise annual checkout until the verified-event activation API is period-aware.
    assert all(item["billing_period"] == "monthly" for item in public_payment_options())


def test_calendar_period_advancement_handles_month_end_and_leap_day():
    jan_31 = datetime(2027, 1, 31, 12, tzinfo=timezone.utc)
    assert _advance_period(jan_31, BILLING_MONTHLY) == datetime(2027, 2, 28, 12, tzinfo=timezone.utc)

    leap_day = datetime(2028, 2, 29, 12, tzinfo=timezone.utc)
    assert _advance_period(leap_day, BILLING_ANNUAL) == datetime(2029, 2, 28, 12, tzinfo=timezone.utc)


def test_annual_subscription_persists_canonical_period_and_amount(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = _approved_user(store, "annual-pro@example.com")
    ledger = SubscriptionLedger(store)

    status = ledger.verify_payment(
        signup.user_id,
        "pro",
        "PAYPAL-ANNUAL-PRO-TEST",
        billing_period=BILLING_ANNUAL,
    )
    state = status["subscription"]
    assert state["billing_period"] == "annual"
    assert status["user"]["plan_id"] == "pro"

    start = datetime.fromisoformat(state["period_start"])
    end = datetime.fromisoformat(state["period_end"])
    assert end == _advance_period(start, BILLING_ANNUAL)

    with sqlite3.connect(store.db_path) as con:
        row = con.execute(
            "SELECT amount, amount_minor, currency, billing_period FROM subscription_payments WHERE payment_reference=?",
            ("PAYPAL-ANNUAL-PRO-TEST",),
        ).fetchone()
    assert row == ("99.00", 9900, "GBP", "annual")
