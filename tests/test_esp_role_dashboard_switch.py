from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_role_dashboard_switch import DashboardPreferenceStore, allowed_modes, router


def _user(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("member@example.com", "Member", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    return accounts, user


def test_allowed_modes_follow_owner_activated_esp_role_only():
    assert allowed_modes({"status": "active", "roles": "creator"}) == ["creator"]
    assert allowed_modes({"status": "active", "roles": "agent"}) == ["agent"]
    assert allowed_modes({"status": "active", "roles": "both"}) == ["creator", "agent"]
    assert allowed_modes({"status": "owner", "roles": "owner"}) == ["creator", "agent"]
    assert allowed_modes({"status": "none", "roles": ""}) == []


def test_creator_only_cannot_switch_to_agent(tmp_path):
    accounts, user = _user(tmp_path)
    store = DashboardPreferenceStore(accounts.db_path)
    membership = {"status": "active", "roles": "creator"}
    assert store.get(user["id"], membership) == "creator"
    with pytest.raises(PermissionError, match="not available"):
        store.set(user["id"], membership, "agent")


def test_agent_only_cannot_switch_to_creator(tmp_path):
    accounts, user = _user(tmp_path)
    store = DashboardPreferenceStore(accounts.db_path)
    membership = {"status": "active", "roles": "agent"}
    assert store.get(user["id"], membership) == "agent"
    with pytest.raises(PermissionError, match="not available"):
        store.set(user["id"], membership, "creator")


def test_creator_plus_agent_can_persist_switch_both_directions(tmp_path):
    accounts, user = _user(tmp_path)
    store = DashboardPreferenceStore(accounts.db_path)
    membership = {"status": "active", "roles": "both"}
    assert store.get(user["id"], membership) == "creator"
    assert store.set(user["id"], membership, "agent") == "agent"
    assert store.get(user["id"], membership) == "agent"
    assert store.set(user["id"], membership, "creator") == "creator"
    assert store.get(user["id"], membership) == "creator"


def test_stored_view_falls_back_if_owner_changes_role(tmp_path):
    accounts, user = _user(tmp_path)
    store = DashboardPreferenceStore(accounts.db_path)
    both = {"status": "active", "roles": "both"}
    store.set(user["id"], both, "agent")
    creator_only = {"status": "active", "roles": "creator"}
    assert store.get(user["id"], creator_only) == "creator"


def test_switch_routes_are_private_and_include_separate_dashboards():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/dashboard" in paths
    assert "/command-center/creator-dashboard" in paths
    assert "/command-center/agent-dashboard" in paths
    assert "/command-center/switch-to/{mode}" in paths
    assert "/command-center/api/dashboard-view" in paths
    assert all(path is None or path.startswith("/command-center/") for path in paths)
