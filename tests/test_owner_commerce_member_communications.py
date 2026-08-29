from __future__ import annotations

import inspect
import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.owner_commerce_member_communications import OwnerCommerceMemberStore, router


def _active_user(tmp_path, email="member@example.com"):
    accounts = AccountStore(tmp_path / "app.sqlite3")
    created = accounts.signup(email, "Member", "strong-password-123", "pro")
    with sqlite3.connect(accounts.db_path) as con:
        con.execute("UPDATE users SET status='active',plan_id='pro',billing_status='active' WHERE id=?", (created.user_id,))
    return accounts, created.user_id


def test_discount_is_owner_configured_scoped_and_transactional(tmp_path):
    accounts, user_id = _active_user(tmp_path)
    store = OwnerCommerceMemberStore(accounts)
    created = store.create_discount(code="mmt2026", percent_off=20, applies_to="subscription", plan_ids=["pro"], max_uses=2)
    assert created["code"] == "MMT2026"
    quote = store.quote_discount(code="MMT2026", user_id=user_id, amount_minor=999, currency="GBP", purchase_kind="subscription", plan_id="pro")
    assert quote["discount_amount_minor"] == 200
    assert quote["net_amount_minor"] == 799
    redeemed = store.redeem_discount(code="MMT2026", user_id=user_id, amount_minor=999, currency="GBP", purchase_kind="subscription", plan_id="pro", purchase_ref="verified-pay-1")
    assert redeemed["net_amount_minor"] == 799
    with pytest.raises(ValueError, match="already been used"):
        store.quote_discount(code="MMT2026", user_id=user_id, amount_minor=999, currency="GBP", purchase_kind="subscription", plan_id="pro")


def test_discount_does_not_mutate_membership_or_billing_state(tmp_path):
    accounts, user_id = _active_user(tmp_path)
    store = OwnerCommerceMemberStore(accounts)
    before = accounts.get_user(user_id)
    store.create_discount(code="KEV20", percent_off=20)
    store.redeem_discount(code="KEV20", user_id=user_id, amount_minor=1000, currency="GBP", purchase_kind="purchase", purchase_ref="purchase-1")
    after = accounts.get_user(user_id)
    assert after["plan_id"] == before["plan_id"]
    assert after["billing_status"] == before["billing_status"]
    assert "esp_" not in " ".join(store._connect().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone() or ())


def test_social_profile_requires_tiktok_and_records_handle_history(tmp_path):
    accounts, user_id = _active_user(tmp_path)
    store = OwnerCommerceMemberStore(accounts)
    with pytest.raises(ValueError, match="TikTok handle is required"):
        store.update_social_profile(user_id, {"instagram": "insta"})
    first = store.update_social_profile(user_id, {"tiktok": "@first.handle", "instagram": "insta"})
    assert first["tiktok"] == "first.handle"
    store.update_social_profile(user_id, {"tiktok": "second.handle", "instagram": "insta"})
    with sqlite3.connect(accounts.db_path) as con:
        changes = con.execute("SELECT old_handle,new_handle FROM member_social_handle_history WHERE user_id=? AND platform='tiktok' ORDER BY changed_at", (user_id,)).fetchall()
    assert changes[-1] == ("first.handle", "second.handle")


def test_social_profile_rejects_urls_and_provider_mismatch_requires_verified_source(tmp_path):
    accounts, user_id = _active_user(tmp_path)
    store = OwnerCommerceMemberStore(accounts)
    with pytest.raises(ValueError, match="handle only"):
        store.update_social_profile(user_id, {"tiktok": "https://tiktok.com/@member"})
    store.update_social_profile(user_id, {"tiktok": "member"})
    with pytest.raises(ValueError, match="Verified provider source"):
        store.set_provider_mismatch(user_id, "tiktok", True, verified_source="")
    store.set_provider_mismatch(user_id, "tiktok", True, verified_source="tiktok-oauth-sync:event-1")
    assert "tiktok" in store.social_profile(user_id)["provider_mismatch"]


def test_monthly_notice_uses_payment_details_only_from_verified_billing_fact(tmp_path):
    accounts, user_id = _active_user(tmp_path)
    store = OwnerCommerceMemberStore(accounts)
    assert store.queue_monthly_membership_notices(period="2026-08") == 1
    with sqlite3.connect(accounts.db_path) as con:
        first = con.execute("SELECT body_text FROM membership_notification_outbox WHERE user_id=?", (user_id,)).fetchone()[0]
    assert "Visit Membership & Billing" in first
    assert "9.99" not in first

    store.record_verified_billing_fact(user_id=user_id, provider="paypal", source_event_ref="verified-event-1", subscription_status="active", next_payment_at="2026-09-29T12:00:00+00:00", next_amount_minor=999, currency="GBP")
    assert store.queue_monthly_membership_notices(period="2026-09") == 1
    with sqlite3.connect(accounts.db_path) as con:
        second = con.execute("SELECT body_text FROM membership_notification_outbox WHERE dedupe_key=?", (f"monthly-membership:2026-09:{user_id}",)).fetchone()[0]
    assert "GBP 9.99 on 2026-09-29" in second


def test_monthly_notice_is_deduplicated(tmp_path):
    accounts, _ = _active_user(tmp_path)
    store = OwnerCommerceMemberStore(accounts)
    assert store.queue_monthly_membership_notices(period="2026-10") == 1
    assert store.queue_monthly_membership_notices(period="2026-10") == 0


def test_owner_commerce_routes_exist_and_are_composed_into_base_api():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/commerce" in paths
    assert "/owner/commerce/users" in paths
    assert "/account/profile-socials" in paths

    import aura_music_studio.api as aggregate
    source = inspect.getsource(aggregate)
    assert "owner_commerce_member_communications_router" in source
    assert "app.include_router(owner_commerce_member_communications_router)" in source
