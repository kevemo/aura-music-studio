from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_learning_library import LearningProgressStore, library_for, router


def _user(accounts: AccountStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def test_creator_library_hides_agent_and_owner_confidential_tracks(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _user(accounts, "creator-library@example.com")
    store = LearningProgressStore(accounts.db_path)
    monkeypatch.setattr("aura_music_studio.esp_learning_library.progress", store)

    data = library_for(user["id"], {"status": "active", "roles": "creator"})
    ids = {row["id"] for row in data["resources"]}
    assert "creator-welcome" in ids
    assert "creator-video-strategy" in ids
    assert "agent-01" not in ids
    assert "agent-advanced" not in ids
    assert "owner-expansion" not in ids
    assert data["role_restricted"] is True
    assert data["long_form_source_exposed"] is False


def test_agent_library_gets_agent_training_but_not_creator_only_or_owner_strategy(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _user(accounts, "agent-library@example.com")
    store = LearningProgressStore(accounts.db_path)
    monkeypatch.setattr("aura_music_studio.esp_learning_library.progress", store)

    data = library_for(user["id"], {"status": "active", "roles": "agent"})
    ids = {row["id"] for row in data["resources"]}
    assert "agent-01" in ids
    assert "agent-advanced" in ids
    assert "creator-health" in ids
    assert "creator-welcome" not in ids
    assert "owner-system-audit" not in ids


def test_owner_library_includes_owner_strategy_and_role_training(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _user(accounts, "owner-library@example.com")
    store = LearningProgressStore(accounts.db_path)
    monkeypatch.setattr("aura_music_studio.esp_learning_library.progress", store)

    data = library_for(user["id"], {"status": "owner", "roles": "owner"})
    ids = {row["id"] for row in data["resources"]}
    assert "owner-expansion" in ids
    assert "owner-system-audit" in ids
    assert "creator-welcome" in ids
    assert "agent-01" in ids


def test_learning_progress_is_user_scoped_and_records_evidence(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = _user(accounts, "learner-one@example.com")
    second = _user(accounts, "learner-two@example.com")
    store = LearningProgressStore(accounts.db_path)

    row = store.set(first["id"], "creator-welcome", "completed", "Completed standards review and chose two goals.")
    assert row["status"] == "completed"
    assert row["completed_at"]
    assert "two goals" in row["evidence_note"]
    assert store.for_user(second["id"]) == {}


def test_learning_library_routes_remain_private():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/library" in paths
    assert "/command-center/api/library" in paths
    assert "/command-center/api/library/{resource_id}/progress" in paths
    assert "/library" not in paths
