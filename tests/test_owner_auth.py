from __future__ import annotations

import hashlib
import sqlite3

from aura_music_studio.owner_auth import OwnerSessionStore, owner_key_matches


def test_owner_session_token_is_random_and_only_hash_is_stored(tmp_path):
    db = tmp_path / "owner.sqlite3"
    sessions = OwnerSessionStore(db)
    token = sessions.create()

    assert token
    assert sessions.valid(token) is True

    with sqlite3.connect(db) as con:
        row = con.execute("SELECT token_hash FROM owner_sessions").fetchone()
    assert row is not None
    assert row[0] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert row[0] != token


def test_owner_session_revocation_is_immediate(tmp_path):
    sessions = OwnerSessionStore(tmp_path / "owner.sqlite3")
    token = sessions.create()
    assert sessions.valid(token) is True
    sessions.revoke(token)
    assert sessions.valid(token) is False


def test_admin_key_is_only_a_bootstrap_credential(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "test-bootstrap-owner-key")
    assert owner_key_matches("test-bootstrap-owner-key") is True
    assert owner_key_matches("wrong-key") is False

    # An owner session is independent from the bootstrap credential and therefore never
    # needs to use the deployment key as its bearer token.
    sessions = OwnerSessionStore(":memory:")
    token = sessions.create()
    assert token != "test-bootstrap-owner-key"
