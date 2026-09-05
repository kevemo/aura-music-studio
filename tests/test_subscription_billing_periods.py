from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.subscriptions import SubscriptionLedger, _advance_billing_period
import aura_music_studio.subscriptions as subscriptions_module


def test_calendar_period_advancement_clamps_month_and_leap_year_boundaries():
    assert _advance_billing_period(
        datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc), BillingPeriod.MONTHLY
    ) == datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
    assert _advance_billing_period(
        datetime(2028, 2, 29, 12, 0, tzinfo=timezone.utc), BillingPeriod.ANNUAL
    ) == datetime(2029, 2, 28, 12, 0, tzinfo=timezone.utc)


def _approved_paid_user(
    tmp_path,
    plan_id: str = "pro",
    *,
    approved_billing_period: BillingPeriod | None = None,
):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("period@example.com", "Period User", "verysecurepassword", plan_id)
    if approved_billing_period is not None:
        preferences = MembershipBillingPreferenceStore(store)
        preferences.record_request(
            user_id=signup.user_id,
            membership_request_id=signup.membership_request_id,
            plan_id=plan_id,
            billing_period=approved_billing_period,
        )
        preferences.decide(signup.membership_request_id, approved=True)
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE users SET status='approved_pending_payment', billing_status='awaiting_payment' WHERE id=?",
            (signup.user_id,),
        )
    return store, signup.user_id


def test_annual_pro_payment_persists_period_price_and_calendar_year(tmp_path, monkeypatch):
    store, user_id = _approved_paid_user(
        tmp_path,
        approved_billing_period=BillingPeriod.ANNUAL,
    )
    fixed_now = datetime(2028, 2, 29, 9, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(subscriptions_module, "_now", lambda: fixed_now)

    ledger = SubscriptionLedger(store)
    status = ledger.verify_payment(
        user_id,
        "pro",
        "ANNUAL-PRO-2028",
        billing_period=BillingPeriod.ANNUAL,
    )

    subscription = status["subscription"]
    assert subscription["billing_period"] == "annual"
    assert subscription["period_start"] == fixed_now.isoformat()
    assert subscription["period_end"] == datetime(2029, 2, 28, 9, 30, tzinfo=timezone.utc).isoformat()

    with sqlite3.connect(store.db_path) as con:
        con.row_factory = sqlite3.Row
        payment = con.execute(
            "SELECT amount,amount_minor,currency,billing_period FROM subscription_payments WHERE payment_reference=?",
            ("ANNUAL-PRO-2028",),
        ).fetchone()
    assert payment is not None
    assert payment["amount"] == "99.00"
    assert payment["amount_minor"] == 9900
    assert payment["currency"] == "GBP"
    assert payment["billing_period"] == "annual"


def test_member_annual_payment_fails_closed(tmp_path):
    store, user_id = _approved_paid_user(tmp_path, "base")
    ledger = SubscriptionLedger(store)
    with pytest.raises(ValueError, match="Annual billing is not available"):
        ledger.verify_payment(
            user_id,
            "base",
            "INVALID-MEMBER-ANNUAL",
            billing_period=BillingPeriod.ANNUAL,
        )


def test_legacy_subscription_rows_migrate_to_monthly_period(tmp_path):
    store, user_id = _approved_paid_user(tmp_path)
    with sqlite3.connect(store.db_path) as con:
        con.executescript(
            """
            CREATE TABLE subscription_state (
                user_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                status TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                last_payment_reference TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE subscription_payments (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                payment_reference TEXT NOT NULL UNIQUE,
                amount_usd TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                verified_at TEXT NOT NULL
            );
            """
        )
        con.execute(
            "INSERT INTO subscription_state VALUES (?,?,?,?,?,?,?)",
            (
                user_id,
                "pro",
                "active",
                "2026-08-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
                "LEGACY",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        con.execute(
            "INSERT INTO subscription_payments VALUES (?,?,?,?,?,?,?,?)",
            (
                "legacy-payment",
                user_id,
                "pro",
                "LEGACY",
                "9.99",
                "2026-08-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
            ),
        )

    ledger = SubscriptionLedger(store)
    state = ledger.get(user_id)
    assert state is not None
    assert state["billing_period"] == "monthly"
    with sqlite3.connect(store.db_path) as con:
        con.row_factory = sqlite3.Row
        payment = con.execute("SELECT billing_period FROM subscription_payments").fetchone()
    assert payment is not None
    assert payment["billing_period"] == "monthly"
