from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.account_recovery as recovery_module
from aura_music_studio.account_recovery import AccountRecoveryStore
from aura_music_studio.accounts import AccountStore, _hash_secret
from aura_music_studio.audit import AuditLedger


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    recovery = AccountRecoveryStore(accounts)
    return accounts, recovery


def _member(accounts: AccountStore, email: str, password: str = "original-password") -> str:
    signup = accounts.signup(email, "Test Member", password, "free")
    return signup.user_id


def test_reset_token_is_hashed_single_use_and_revokes_every_old_session(tmp_path):
    accounts, recovery = _stores(tmp_path)
    user_id = _member(accounts, "reset@example.com")
    first_session = accounts.create_session(user_id)
    second_session = accounts.create_session(user_id)

    issue = recovery.issue_password_reset("reset@example.com")
    assert issue is not None
    assert recovery.reset_token_valid(issue.token) is True

    with sqlite3.connect(accounts.db_path) as con:
        row = con.execute(
            "SELECT token_hash FROM password_reset_tokens WHERE user_id=?",
            (user_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == _hash_secret(issue.token)
    assert issue.token != row[0]

    result = recovery.consume_password_reset(issue.token, "replacement-password")
    assert result["user_id"] == user_id
    assert result["sessions_revoked"] is True
    assert recovery.reset_token_valid(issue.token) is False
    assert accounts.resolve_session(first_session) is None
    assert accounts.resolve_session(second_session) is None
    assert accounts.authenticate("reset@example.com", "replacement-password") is not None
    assert accounts.authenticate("reset@example.com", "original-password") is None

    with pytest.raises(ValueError, match="invalid|used"):
        recovery.consume_password_reset(issue.token, "another-password")


def test_new_reset_request_invalidates_previous_unused_link(tmp_path):
    accounts, recovery = _stores(tmp_path)
    user_id = _member(accounts, "rotate@example.com")
    first = recovery.issue_password_reset("rotate@example.com")
    assert first is not None

    # Move the request throttle outside its one-minute minimum interval without weakening
    # the production limit itself.
    with sqlite3.connect(accounts.db_path) as con:
        con.execute(
            "UPDATE auth_password_reset_throttle SET last_requested_at='2026-01-01T00:00:00+00:00'"
        )

    second = recovery.issue_password_reset("rotate@example.com")
    assert second is not None
    assert second.token != first.token
    assert recovery.reset_token_valid(first.token) is False
    assert recovery.reset_token_valid(second.token) is True

    with sqlite3.connect(accounts.db_path) as con:
        active = con.execute(
            """SELECT COUNT(*) FROM password_reset_tokens
               WHERE user_id=? AND used_at IS NULL AND invalidated_at IS NULL""",
            (user_id,),
        ).fetchone()[0]
    assert active == 1


def test_reset_request_throttle_applies_to_known_and_unknown_identities(tmp_path):
    accounts, recovery = _stores(tmp_path)
    _member(accounts, "known@example.com")

    assert recovery.issue_password_reset("known@example.com") is not None
    assert recovery.issue_password_reset("known@example.com") is None
    assert recovery.issue_password_reset("missing@example.com") is None
    assert recovery.issue_password_reset("missing@example.com") is None

    with sqlite3.connect(accounts.db_path) as con:
        rows = con.execute(
            "SELECT identity_hash,request_count FROM auth_password_reset_throttle"
        ).fetchall()
    assert len(rows) == 2
    assert all("@" not in identity for identity, _ in rows)


def test_session_inventory_revoke_others_and_cross_user_isolation(tmp_path):
    accounts, recovery = _stores(tmp_path)
    user_a = _member(accounts, "a@example.com", "a-password-123")
    user_b = _member(accounts, "b@example.com", "b-password-123")
    a_current = accounts.create_session(user_a)
    a_other = accounts.create_session(user_a)
    b_session = accounts.create_session(user_b)

    sessions = recovery.list_sessions(user_a, a_current)
    assert len(sessions) == 2
    assert sum(1 for item in sessions if item["current"]) == 1

    b_session_id = recovery.current_session_id(b_session)
    assert b_session_id is not None
    assert recovery.revoke_session(user_a, b_session_id) is False
    assert accounts.resolve_session(b_session) is not None

    assert recovery.revoke_other_sessions(user_a, a_current) == 1
    assert accounts.resolve_session(a_current) is not None
    assert accounts.resolve_session(a_other) is None
    assert accounts.resolve_session(b_session) is not None


def test_authenticated_password_change_preserves_current_session_only(tmp_path):
    accounts, recovery = _stores(tmp_path)
    user_id = _member(accounts, "change@example.com", "current-password")
    current = accounts.create_session(user_id)
    other = accounts.create_session(user_id)

    revoked = recovery.change_password(
        user_id,
        current,
        "current-password",
        "new-password-123",
    )
    assert revoked == 1
    assert accounts.resolve_session(current) is not None
    assert accounts.resolve_session(other) is None
    assert accounts.authenticate("change@example.com", "new-password-123") is not None
    assert accounts.authenticate("change@example.com", "current-password") is None

    with pytest.raises(ValueError, match="different"):
        recovery.change_password(
            user_id,
            current,
            "new-password-123",
            "new-password-123",
        )


def test_public_reset_request_response_does_not_enumerate_accounts(tmp_path, monkeypatch):
    accounts, recovery = _stores(tmp_path)
    _member(accounts, "visible@example.com")
    monkeypatch.setattr(recovery_module, "account_store", accounts)
    monkeypatch.setattr(recovery_module, "store", recovery)
    monkeypatch.setattr(recovery_module, "audit", AuditLedger(accounts))
    sent: list[str] = []
    monkeypatch.setattr(
        recovery_module,
        "_send_reset_email",
        lambda issue: sent.append(issue.email),
    )

    app = FastAPI()
    app.include_router(recovery_module.router)
    client = TestClient(app)

    known = client.post(
        "/auth/password-reset/request",
        json={"email": "visible@example.com"},
    )
    missing = client.post(
        "/auth/password-reset/request",
        json={"email": "not-there@example.com"},
    )
    assert known.status_code == 200
    assert missing.status_code == 200
    assert known.json() == missing.json()
    assert known.json() == {
        "accepted": True,
        "message": "If an eligible account matches that email, a password reset link will be sent.",
    }
    assert sent == ["visible@example.com"]
    assert "token" not in json.dumps(known.json()).lower()


def test_recovery_routes_are_explicitly_mounted_and_branch_cannot_deploy_to_vercel():
    source = Path("aura_music_studio/api.py").read_text(encoding="utf-8")
    assert "from .account_recovery import router as account_recovery_router" in source
    assert "app.include_router(account_recovery_router)" in source

    vercel = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    assert vercel["git"]["deploymentEnabled"]["feature/core-password-recovery-sessions"] is False
