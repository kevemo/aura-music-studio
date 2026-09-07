from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.plans import (
    APPROVED_VOICE_DUPLICATION,
    AURASEC,
    FULL_TRACK,
    SAMPLE_LAB,
    STEM_SPLITTER,
    get_plan,
)
from aura_music_studio.subscriptions import SubscriptionLedger


def _approved_paid_user(store: AccountStore, email: str, plan_id: str):
    signup = store.signup(email, "Test Member", "very-secure-password", plan_id)
    pending = store.membership_request_from_token(signup.approval_token)
    assert pending and pending["status"] == "pending"
    approved = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert approved["status"] == "approved_pending_payment"
    ledger = SubscriptionLedger(store)
    status = ledger.verify_payment(signup.user_id, plan_id, f"PAYPAL-{plan_id}-TEST-REFERENCE")
    active = status["user"]
    assert active["status"] == "active"
    assert active["plan_id"] == plan_id
    assert status["subscription"]["status"] == "active"
    return active


def test_plan_progression():
    free = get_plan("free")
    base = get_plan("base")
    pro = get_plan("pro")

    assert FULL_TRACK not in free.features
    assert FULL_TRACK in base.features
    assert STEM_SPLITTER not in base.features
    assert STEM_SPLITTER in pro.features
    assert SAMPLE_LAB not in base.features
    assert SAMPLE_LAB in pro.features
    assert APPROVED_VOICE_DUPLICATION not in base.features
    assert APPROVED_VOICE_DUPLICATION in pro.features
    assert AURASEC not in free.features
    assert AURASEC not in base.features
    assert AURASEC in pro.features
    assert base.confirmed_songs_per_day == 1
    assert pro.confirmed_songs_per_day is None


def test_public_membership_pricing_contract():
    free = get_plan("free")
    basic = get_plan("base")
    pro = get_plan("pro")

    assert free.monthly_price == Decimal("0.00")
    assert free.monthly_price_minor == 0
    assert free.display_price == "Free"

    # Keep the persisted identifier stable while exposing the approved customer-facing tier.
    assert basic.id == "base"
    assert basic.name == "Basic"
    assert basic.currency == "GBP"
    assert basic.monthly_price == Decimal("4.99")
    assert basic.monthly_price_minor == 499
    assert basic.display_price == "£4.99"

    assert pro.id == "pro"
    assert pro.name == "Unlimited Pro"
    assert pro.currency == "GBP"
    assert pro.monthly_price == Decimal("9.99")
    assert pro.monthly_price_minor == 999
    assert pro.display_price == "£9.99"
    assert AURASEC in pro.public_dict()["features"]


def test_free_activates_after_owner_approval(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("free@example.com", "Free Member", "very-secure-password", "free")
    user = store.get_user(signup.user_id)
    assert user["status"] == "pending_approval"
    approved = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert approved["status"] == "active"
    assert approved["plan_id"] == "free"
    assert approved["billing_status"] == "not_required"


def test_base_is_one_confirmed_track_per_day_with_unlimited_preconfirm_regens(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user = _approved_paid_user(store, "base@example.com", "base")
    day = "2026-08-23"

    slot = store.start_song_slot(user["id"], "song-one", day)
    assert slot["state"] == "draft"

    for expected in range(1, 6):
        slot = store.record_regeneration(user["id"], "song-one")
        assert slot["generation_count"] == expected
        assert slot["state"] == "draft"

    confirmed = store.confirm_song(user["id"], "song-one")
    assert confirmed["state"] == "confirmed"

    with pytest.raises(PermissionError):
        store.start_song_slot(user["id"], "song-two", day)


def test_paid_plan_cannot_activate_before_esp_approval(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    ledger = SubscriptionLedger(store)
    signup = store.signup("pending@example.com", "Pending Member", "very-secure-password", "pro")
    with pytest.raises(ValueError):
        ledger.verify_payment(signup.user_id, "pro", "PAYPAL-TEST")


def test_verified_payment_creates_current_term_and_schedules_prepaid_renewal(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("renew@example.com", "Renewing Member", "very-secure-password", "pro")
    store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    ledger = SubscriptionLedger(store)

    first = ledger.verify_payment(signup.user_id, "pro", "PAYPAL-MONTH-ONE")
    first_end = datetime.fromisoformat(first["subscription"]["period_end"])
    second = ledger.verify_payment(signup.user_id, "pro", "PAYPAL-MONTH-TWO")

    # A second verified payment is a separately paid future term. Do not mutate the current
    # entitlement early: preserving the boundary makes refunds and cancellation reversible.
    assert datetime.fromisoformat(second["subscription"]["period_end"]) == first_end
    transition = second["scheduled_transition"]
    assert transition["target_plan_id"] == "pro"
    assert transition["target_billing_period"] == "monthly"
    assert datetime.fromisoformat(transition["effective_at"]) == first_end
    assert datetime.fromisoformat(transition["period_end"]) > first_end

    with pytest.raises(ValueError):
        ledger.verify_payment(signup.user_id, "pro", "PAYPAL-MONTH-TWO")


def test_rejected_request_cannot_be_approved_twice(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("reject@example.com", "Rejected Member", "very-secure-password", "base")
    rejected = store.decide_membership(signup.approval_token, "reject", "ESP Test Owner")
    assert rejected["status"] == "rejected"
    with pytest.raises(ValueError):
        store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
