from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_tech_vault import CreatorTechVaultStore, TechProfileCreate, VerificationCreate


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    return accounts, CreatorTechVaultStore(esp)


def _user(accounts: AccountStore, email: str) -> dict:
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def test_vault_requires_consent_and_rejects_secrets(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    with pytest.raises(ValueError, match="explicit creator consent"):
        store.create_version(creator["id"], TechProfileCreate(consent_store_setup=False), actor=creator["id"])
    with pytest.raises(ValueError, match="must not store"):
        store.create_version(
            creator["id"],
            TechProfileCreate(consent_store_setup=True, scene_notes="stream key: abc-secret"),
            actor=creator["id"],
        )


def test_new_device_or_software_change_creates_new_version_not_overwrite(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    first = store.create_version(
        creator["id"],
        TechProfileCreate(consent_store_setup=True, operating_system="Windows 11", microphone="Mic A", change_reason="Initial setup"),
        actor=creator["id"],
    )
    second = store.create_version(
        creator["id"],
        TechProfileCreate(consent_store_setup=True, operating_system="Windows 11", microphone="Mic B", change_reason="New microphone"),
        actor=creator["id"],
    )
    assert first["version_no"] == 1
    assert second["version_no"] == 2
    history = store.history(creator["id"], requester_user_id=creator["id"])
    assert [row["version_no"] for row in history] == [2, 1]
    assert history[0]["config"]["microphone"] == "Mic B"
    assert history[1]["config"]["microphone"] == "Mic A"
    assert history[1]["retired_at"] is not None


def test_known_good_requires_recorded_verification(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    profile = store.create_version(
        creator["id"],
        TechProfileCreate(consent_store_setup=True, live_software="OBS", output_resolution="1080x1920", fps="30"),
        actor=creator["id"],
    )
    assert profile["known_good"] is False
    verified = store.verify(
        profile["id"],
        VerificationCreate(outcome="known_good", test_type="local_recording_and_test_live", duration_minutes=10, conditions="Wired Ethernet"),
        actor="tech-owner",
    )
    assert verified["known_good"] is True
    assert verified["known_good_test"]["duration_minutes"] == 10


def test_creator_cannot_read_another_creators_configuration(tmp_path):
    accounts, store = _stores(tmp_path)
    first = _user(accounts, "first@example.com")
    second = _user(accounts, "second@example.com")
    profile = store.create_version(first["id"], TechProfileCreate(consent_store_setup=True, microphone="Mic"), actor=first["id"])
    with pytest.raises(PermissionError):
        store.get_version(profile["id"], user_id=second["id"])
    assert store.get_version(profile["id"], owner=True)["user_id"] == first["id"]


def test_revoke_consent_deletes_configuration_history(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    store.create_version(creator["id"], TechProfileCreate(consent_store_setup=True, camera="Camera A"), actor=creator["id"])
    store.revoke_consent(creator["id"], actor=creator["id"])
    assert store.current(creator["id"], requester_user_id=creator["id"]) is None
    assert store.history(creator["id"], requester_user_id=creator["id"]) == []
