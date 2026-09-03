from __future__ import annotations

from datetime import datetime, timezone

from aura_music_studio.accounts import AccountStore
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.subscriptions import SubscriptionLedger
import aura_music_studio.subscriptions as subscriptions_module


def _approved_user(tmp_path, monkeypatch, *, plan_id: str = "base", period: BillingPeriod = BillingPeriod.MONTHLY):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("lifecycle@example.com", "Lifecycle User", "verysecurepassword", plan_id)
    prefs = MembershipBillingPreferenceStore(store)
    prefs.record_request(
        user_id=signup.user_id,
        membership_request_id=signup.membership_request_id,
        plan_id=plan_id,
        billing_period=period,
    )
    store.decide_membership(signup.approval_token, "approve", "Kev")
    prefs.decide(signup.membership_request_id, approved=True)
    return store, signup.user_id


def _at(monkeypatch, value: datetime) -> None:
    monkeypatch.setattr(subscriptions_module, "_now", lambda: value)


def test_verified_cross_plan_payment_is_scheduled_not_granted_early(tmp_path, monkeypatch):
    start = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    _at(monkeypatch, start)
    store, user_id = _approved_user(tmp_path, monkeypatch)
    ledger = SubscriptionLedger(store)

    initial = ledger.verify_payment(user_id, "base", "BASIC-CURRENT")
    current_end = datetime.fromisoformat(initial["subscription"]["period_end"])
    assert initial["user"]["plan_id"] == "base"

    _at(monkeypatch, datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc))
    scheduled = ledger.verify_payment(
        user_id,
        "pro",
        "PRO-FUTURE",
        billing_period=BillingPeriod.ANNUAL,
    )

    assert scheduled["user"]["plan_id"] == "base"
    assert scheduled["subscription"]["plan_id"] == "base"
    assert scheduled["scheduled_transition"]["target_plan_id"] == "pro"
    assert scheduled["scheduled_transition"]["target_billing_period"] == "annual"
    assert scheduled["scheduled_transition"]["effective_at"] == current_end.isoformat()

    _at(monkeypatch, current_end)
    applied_user = ledger.enforce(store.get_user(user_id))
    applied = ledger.get(user_id)
    assert applied_user["plan_id"] == "pro"
    assert applied["plan_id"] == "pro"
    assert applied["billing_period"] == "annual"
    assert ledger.scheduled_transition(user_id) is None


def test_cross_period_payment_on_same_plan_waits_for_current_term_end(tmp_path, monkeypatch):
    start = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    _at(monkeypatch, start)
    store, user_id = _approved_user(tmp_path, monkeypatch, plan_id="pro")
    ledger = SubscriptionLedger(store)
    initial = ledger.verify_payment(user_id, "pro", "PRO-MONTH-CURRENT")
    current_end = datetime.fromisoformat(initial["subscription"]["period_end"])

    _at(monkeypatch, datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc))
    result = ledger.verify_payment(user_id, "pro", "PRO-YEAR-FUTURE", billing_period="annual")
    assert result["subscription"]["billing_period"] == "monthly"
    assert result["scheduled_transition"]["target_billing_period"] == "annual"

    _at(monkeypatch, current_end)
    ledger.enforce(store.get_user(user_id))
    assert ledger.get(user_id)["billing_period"] == "annual"


def test_cancel_at_period_end_preserves_current_paid_access_then_returns_free(tmp_path, monkeypatch):
    start = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    _at(monkeypatch, start)
    store, user_id = _approved_user(tmp_path, monkeypatch)
    ledger = SubscriptionLedger(store)
    paid = ledger.verify_payment(user_id, "base", "BASIC-CANCEL")
    end = datetime.fromisoformat(paid["subscription"]["period_end"])

    canceled = ledger.cancel_at_period_end(user_id)
    assert canceled["user"]["plan_id"] == "base"
    assert canceled["subscription"]["status"] == "cancel_at_period_end"

    _at(monkeypatch, end.replace(microsecond=0) - subscriptions_module.calendar.timedelta(seconds=1) if False else datetime(2026, 10, 4, 8, 59, 59, tzinfo=timezone.utc))
    assert ledger.enforce(store.get_user(user_id))["plan_id"] == "base"

    _at(monkeypatch, end)
    expired = ledger.enforce(store.get_user(user_id))
    assert expired["plan_id"] == "free"
    assert ledger.get(user_id)["status"] == "canceled"


def test_cancel_with_prepaid_future_transition_preserves_both_paid_terms(tmp_path, monkeypatch):
    start = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    _at(monkeypatch, start)
    store, user_id = _approved_user(tmp_path, monkeypatch)
    ledger = SubscriptionLedger(store)
    current = ledger.verify_payment(user_id, "base", "BASIC-FIRST")
    first_end = datetime.fromisoformat(current["subscription"]["period_end"])

    _at(monkeypatch, datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc))
    scheduled = ledger.verify_payment(user_id, "pro", "PRO-PREPAID")
    future_end = datetime.fromisoformat(scheduled["scheduled_transition"]["period_end"])
    canceled = ledger.cancel_at_period_end(user_id)
    assert canceled["scheduled_transition"]["cancel_at_period_end"] == 1
    assert canceled["user"]["plan_id"] == "base"

    _at(monkeypatch, first_end)
    applied = ledger.enforce(store.get_user(user_id))
    assert applied["plan_id"] == "pro"
    assert ledger.get(user_id)["status"] == "cancel_at_period_end"

    _at(monkeypatch, future_end)
    ended = ledger.enforce(store.get_user(user_id))
    assert ended["plan_id"] == "free"
    assert ledger.get(user_id)["status"] == "canceled"


def test_refund_of_future_transition_preserves_current_entitlement(tmp_path, monkeypatch):
    start = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    _at(monkeypatch, start)
    store, user_id = _approved_user(tmp_path, monkeypatch)
    ledger = SubscriptionLedger(store)
    ledger.verify_payment(user_id, "base", "BASIC-KEEP")

    _at(monkeypatch, datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc))
    ledger.verify_payment(user_id, "pro", "PRO-REFUND-FUTURE")
    refunded = ledger.record_verified_refund(user_id, "PRO-REFUND-FUTURE", "REFUND-FUTURE-1")

    assert refunded["refund_outcome"] == "future_transition_refunded_current_term_preserved"
    assert refunded["user"]["plan_id"] == "base"
    assert refunded["subscription"]["plan_id"] == "base"
    assert refunded["scheduled_transition"] is None


def test_refund_of_current_term_revokes_only_current_paid_entitlement(tmp_path, monkeypatch):
    start = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    _at(monkeypatch, start)
    store, user_id = _approved_user(tmp_path, monkeypatch)
    ledger = SubscriptionLedger(store)
    ledger.verify_payment(user_id, "base", "BASIC-REFUND-NOW")

    refunded = ledger.record_verified_refund(user_id, "BASIC-REFUND-NOW", "REFUND-CURRENT-1")
    assert refunded["refund_outcome"] == "current_term_refunded_entitlement_revoked"
    assert refunded["user"]["plan_id"] == "free"
    assert refunded["subscription"]["status"] == "refunded"
