from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.subscriptions import SubscriptionLedger


def _approved(store: AccountStore, email: str, plan_id: str):
    signup = store.signup(email, "Annual Billing Test", "very-secure-password", plan_id)
    approved = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert approved["status"] == "approved_pending_payment"
    return signup


def test_unlimited_pro_annual_payment_persists_period_and_uses_365_day_entitlement(tmp_path):
    store = AccountStore(tmp_path / "annual.sqlite3")
    signup = _approved(store, "annual-pro@example.com", "pro")
    ledger = SubscriptionLedger(store)

    result = ledger.verify_payment(
        signup.user_id,
        "pro",
        "STRIPE-ANNUAL-PRO-001",
        billing_period=BillingPeriod.ANNUAL,
    )

    subscription = result["subscription"]
    assert result["user"]["plan_id"] == "pro"
    assert subscription["billing_period"] == "annual"
    start = datetime.fromisoformat(subscription["period_start"])
    end = datetime.fromisoformat(subscription["period_end"])
    assert (end - start).days == 365

    with sqlite3.connect(store.db_path) as con:
        row = con.execute(
            """SELECT amount,amount_minor,currency,billing_period
               FROM subscription_payments WHERE payment_reference=?""",
            ("STRIPE-ANNUAL-PRO-001",),
        ).fetchone()
    assert row == ("99.00", 9900, "GBP", "annual")


def test_member_annual_payment_fails_closed_because_no_member_annual_price_is_authorised(tmp_path):
    store = AccountStore(tmp_path / "annual-member.sqlite3")
    signup = _approved(store, "annual-member@example.com", "base")
    ledger = SubscriptionLedger(store)

    with pytest.raises(ValueError, match="Annual billing is not available"):
        ledger.verify_payment(
            signup.user_id,
            "base",
            "STRIPE-ANNUAL-MEMBER-001",
            billing_period=BillingPeriod.ANNUAL,
        )

    with sqlite3.connect(store.db_path) as con:
        count = con.execute("SELECT COUNT(*) FROM subscription_payments").fetchone()[0]
    assert count == 0


def test_existing_monthly_contract_remains_default_and_31_days(tmp_path):
    store = AccountStore(tmp_path / "monthly-compat.sqlite3")
    signup = _approved(store, "monthly-pro@example.com", "pro")
    ledger = SubscriptionLedger(store)

    result = ledger.verify_payment(signup.user_id, "pro", "STRIPE-MONTHLY-PRO-001")
    subscription = result["subscription"]
    assert subscription["billing_period"] == "monthly"
    start = datetime.fromisoformat(subscription["period_start"])
    end = datetime.fromisoformat(subscription["period_end"])
    assert (end - start).days == 31

    with sqlite3.connect(store.db_path) as con:
        row = con.execute(
            """SELECT amount,amount_minor,currency,billing_period
               FROM subscription_payments WHERE payment_reference=?""",
            ("STRIPE-MONTHLY-PRO-001",),
        ).fetchone()
    assert row == ("9.99", 999, "GBP", "monthly")
