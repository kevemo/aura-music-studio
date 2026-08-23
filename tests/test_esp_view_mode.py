from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_view_mode import EspViewModeStore


def _active_free_user(store: AccountStore, email: str):
    signup = store.signup(email, "ESP View Test", "very-secure-password", "free")
    user = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert user["status"] == "active"
    return user


def _esp_user(store: AccountStore, esp: EspStore, email: str, role: str):
    user = _active_free_user(store, email)
    _item, token = esp.request_access(user["id"], role, "view.test", "UK+", "view test")
    return esp.decide(token, "approve", role, "ESP Test Owner")


def test_creator_is_locked_to_creator_view(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    views = EspViewModeStore(store)
    user = _esp_user(store, esp, "creator@example.com", "creator")

    context = views.get(user["id"])
    assert context["view"] == "creator"
    assert context["allowed_views"] == ["creator"]
    assert views.command_center_fragment(user["id"]) == ""

    with pytest.raises(PermissionError, match="cannot use"):
        views.set(user["id"], "agent")


def test_agent_defaults_to_agent_and_can_switch_to_creator(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    views = EspViewModeStore(store)
    user = _esp_user(store, esp, "agent@example.com", "agent")

    assert views.get(user["id"])["view"] == "agent"
    switched = views.set(user["id"], "creator")
    assert switched["view"] == "creator"
    assert switched["allowed_views"] == ["agent", "creator"]
    assert views.get(user["id"])["view"] == "creator"

    fragment = views.command_center_fragment(user["id"])
    assert "Agent View" in fragment
    assert "Creator View" in fragment
    assert "/esp/view-mode" in fragment
    assert "aura:esp-view" in fragment


def test_both_role_can_switch_both_directions(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    views = EspViewModeStore(store)
    user = _esp_user(store, esp, "both@example.com", "both")

    assert views.get(user["id"])["view"] == "agent"
    assert views.set(user["id"], "creator")["view"] == "creator"
    assert views.set(user["id"], "agent")["view"] == "agent"


def test_ordinary_customer_cannot_use_esp_view_context(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    EspStore(store)
    views = EspViewModeStore(store)
    user = _active_free_user(store, "ordinary@example.com")

    with pytest.raises(PermissionError, match="ESP membership"):
        views.get(user["id"])
    with pytest.raises(PermissionError, match="ESP membership"):
        views.set(user["id"], "creator")
    assert views.command_center_fragment(user["id"]) == ""


def test_view_mode_is_user_isolated(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    views = EspViewModeStore(store)
    one = _esp_user(store, esp, "one@example.com", "agent")
    two = _esp_user(store, esp, "two@example.com", "agent")

    views.set(one["id"], "creator")
    assert views.get(one["id"])["view"] == "creator"
    assert views.get(two["id"])["view"] == "agent"


def test_invalid_view_mode_is_rejected(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    views = EspViewModeStore(store)
    user = _esp_user(store, esp, "invalid@example.com", "both")

    with pytest.raises(ValueError, match="creator or agent"):
        views.set(user["id"], "admin")
