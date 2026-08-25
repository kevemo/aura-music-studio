from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_connectors import ConnectorVault, _service_list


def _user(accounts: AccountStore, email: str) -> str:
    return accounts.signup(email, "Connector User", "very-long-test-password", "free").user_id


def test_connector_tokens_are_encrypted_and_status_never_exposes_them(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_CONNECTOR_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "connector@example.com")
    chat = AuraChatStore(accounts)
    vault = ConnectorVault(chat)
    token = {
        "access_token": "plain-access-secret",
        "refresh_token": "plain-refresh-secret",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    vault.save(user_id, "google", token, ["drive", "gmail"], "member@example.com")

    with chat._connect() as con:
        row = con.execute(
            "SELECT encrypted_token FROM aura_connectors WHERE user_id=? AND provider='google'",
            (user_id,),
        ).fetchone()
    stored = str(row["encrypted_token"])
    assert "plain-access-secret" not in stored
    assert "plain-refresh-secret" not in stored

    loaded = vault.load(user_id, "google")
    assert loaded["token"]["access_token"] == "plain-access-secret"
    public = vault.status(user_id)
    assert public == [
        {
            "provider": "google",
            "services": ["drive", "gmail"],
            "account_label": "member@example.com",
            "updated_at": public[0]["updated_at"],
            "encrypted_at_rest": True,
            "read_only": True,
        }
    ]
    dumped = json.dumps(public)
    assert "access_token" not in dumped
    assert "refresh_token" not in dumped
    assert "plain-access-secret" not in dumped


def test_oauth_state_is_user_bound_one_time_and_expires_by_consumption(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_CONNECTOR_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "connector-a@example.com")
    user_b = _user(accounts, "connector-b@example.com")
    chat = AuraChatStore(accounts)
    vault = ConnectorVault(chat)

    state = vault.create_state(user_a, "google", ["drive", "calendar"])
    with pytest.raises(PermissionError):
        vault.consume_state(user_b, state, "google")

    assert vault.consume_state(user_a, state, "google") == ["drive", "calendar"]
    with pytest.raises(PermissionError):
        vault.consume_state(user_a, state, "google")


def test_connector_records_are_private_per_member(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_CONNECTOR_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "private-a@example.com")
    user_b = _user(accounts, "private-b@example.com")
    chat = AuraChatStore(accounts)
    vault = ConnectorVault(chat)
    vault.save(user_a, "google", {"access_token": "a"}, ["drive"], "a@example.com")
    assert len(vault.status(user_a)) == 1
    assert vault.status(user_b) == []
    assert vault.load(user_b, "google") is None


def test_service_scope_filtering_is_allowlisted():
    assert _service_list("drive,calendar,gmail,drive,admin,unknown") == ["drive", "calendar", "gmail"]
    assert _service_list(["gmail"]) == ["gmail"]
    assert _service_list([]) == ["drive", "calendar", "gmail"]
