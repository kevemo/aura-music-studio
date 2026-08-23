from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore, RESOURCE_CATALOG, _resource_allowed
from aura_music_studio.subscriptions import SubscriptionLedger


def _active_free_user(store: AccountStore, email: str):
    signup = store.signup(email, "ESP Test Member", "very-secure-password", "free")
    user = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert user["status"] == "active"
    assert user["plan_id"] == "free"
    return signup, user


def _approve_esp(store: AccountStore, esp: EspStore, email: str, role: str = "creator"):
    signup, user = _active_free_user(store, email)
    item, token = esp.request_access(user["id"], role, "esp.test.creator", "UK+", "test request")
    assert item["status"] == "pending"
    approved = esp.decide(token, "approve", role, "ESP Test Owner")
    assert approved["status"] == "active"
    return signup, approved


def test_esp_approval_grants_base_without_payment(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    _signup, approved = _approve_esp(store, esp, "creator@example.com", "creator")

    assert approved["plan_id"] == "base"
    assert approved["billing_status"] == "esp_comped"
    membership = esp.membership(approved["id"])
    assert membership["status"] == "active"
    assert membership["roles"] == "creator"

    enforced = SubscriptionLedger(store).enforce(store.get_user(approved["id"]))
    assert enforced["status"] == "active"
    assert enforced["plan_id"] == "base"
    assert enforced["billing_status"] == "esp_comped"


def test_esp_revoke_removes_comped_base(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    _signup, approved = _approve_esp(store, esp, "revoke@example.com", "agent")

    esp.revoke(approved["id"], "ESP Test Owner")
    user = store.get_user(approved["id"])
    membership = esp.membership(approved["id"])

    assert membership["status"] == "revoked"
    assert membership["roles"] == ""
    assert user["plan_id"] == "free"
    assert user["billing_status"] == "not_required"


def test_expired_paid_pro_falls_back_to_esp_base(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    _signup, approved = _approve_esp(store, esp, "pro@example.com", "both")
    ledger = SubscriptionLedger(store)

    paid = ledger.verify_payment(approved["id"], "pro", "PAYPAL-PRO-ESP-TEST")
    assert paid["user"]["plan_id"] == "pro"
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE subscription_state SET period_end=?,status='active' WHERE user_id=?",
            (expired, approved["id"]),
        )

    enforced = ledger.enforce(store.get_user(approved["id"]))
    assert enforced["status"] == "active"
    assert enforced["plan_id"] == "base"
    assert enforced["billing_status"] == "esp_comped"


def test_owner_can_switch_creator_agent_and_both(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    _signup, approved = _approve_esp(store, esp, "roles@example.com", "creator")

    esp.set_role(approved["id"], "agent", "Mary")
    assert esp.membership(approved["id"])["roles"] == "agent"
    esp.set_role(approved["id"], "both", "Kev")
    assert esp.membership(approved["id"])["roles"] == "both"


def test_role_gates_keep_agent_training_away_from_creator_only_accounts():
    creator = {"status": "active", "roles": "creator"}
    agent = {"status": "active", "roles": "agent"}
    both = {"status": "active", "roles": "both"}

    assert _resource_allowed(RESOURCE_CATALOG["creator-companion"], creator)
    assert not _resource_allowed(RESOURCE_CATALOG["agent-apprentice"], creator)
    assert _resource_allowed(RESOURCE_CATALOG["agent-apprentice"], agent)
    assert _resource_allowed(RESOURCE_CATALOG["agent-apprentice"], both)
    assert _resource_allowed(RESOURCE_CATALOG["growth-blueprint"], both)


def test_training_progress_and_usage_are_tracked(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    _signup, approved = _approve_esp(store, esp, "progress@example.com", "creator")

    esp.set_progress(approved["id"], "creator-companion", 75)
    esp.log_resource(approved["id"], "creator-companion", "open")
    progress = esp.progress(approved["id"])
    stats = esp.dashboard_stats()

    assert progress["creator-companion"] == 75
    assert stats["active"] == 1
    assert stats["resource_events"] >= 1
