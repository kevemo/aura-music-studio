from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore


def _disable(store: AccountStore, user_id: str) -> None:
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE users SET disabled_at=? WHERE id=?",
            ("2026-09-06T08:00:00+00:00", user_id),
        )


def test_disabled_account_credentials_and_existing_session_fail_closed(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("creator@example.com", "Creator", "correct-horse-battery", "free")

    # Pending applicants are intentionally allowed to sign in to view approval state.
    assert store.authenticate(signup.email, "correct-horse-battery") is not None
    token = store.create_session(signup.user_id)
    assert store.resolve_session(token) is not None

    _disable(store, signup.user_id)

    # Disabling the account invalidates the canonical authentication boundary even
    # when the password is still correct and an existing unexpired session exists.
    assert store.authenticate(signup.email, "correct-horse-battery") is None
    assert store.resolve_session(token) is None

    with pytest.raises(PermissionError, match="Eligible account required"):
        store.create_session(signup.user_id)


def test_non_disabled_pending_account_keeps_existing_signin_semantics(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("pending@example.com", "Pending Creator", "another-strong-password", "free")

    user = store.authenticate(signup.email, "another-strong-password")
    assert user is not None
    assert user["status"] == "pending_approval"

    token = store.create_session(signup.user_id)
    resolved = store.resolve_session(token)
    assert resolved is not None
    assert resolved["id"] == signup.user_id
