from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from aura_music_studio.owner_auth import OWNER_SESSION_IDLE_MINUTES, OwnerSessionStore


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_last_seen(store: OwnerSessionStore, token: str, value: str | None) -> None:
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE owner_sessions SET last_seen_at=? WHERE token_hash=?",
            (value, _token_hash(token)),
        )


def _session_row(store: OwnerSessionStore, token: str) -> sqlite3.Row:
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM owner_sessions WHERE token_hash=?",
            (_token_hash(token),),
        ).fetchone()
        assert row is not None
        return row
    finally:
        con.close()


def test_owner_session_expires_and_revokes_after_idle_timeout(tmp_path):
    store = OwnerSessionStore(tmp_path / "owner.sqlite3")
    token = store.create()
    stale = datetime.now(timezone.utc) - timedelta(minutes=OWNER_SESSION_IDLE_MINUTES + 1)
    _set_last_seen(store, token, stale.isoformat())

    assert store.valid(token) is False

    row = _session_row(store, token)
    assert row["revoked_at"] is not None


def test_owner_session_with_fresh_activity_remains_valid_and_touches_last_seen(tmp_path):
    store = OwnerSessionStore(tmp_path / "owner.sqlite3")
    token = store.create()
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    _set_last_seen(store, token, recent.isoformat())

    before = _session_row(store, token)["last_seen_at"]
    assert store.valid(token) is True
    after = _session_row(store, token)["last_seen_at"]

    assert after > before
    assert _session_row(store, token)["revoked_at"] is None


def test_owner_session_with_missing_or_malformed_activity_timestamp_fails_closed(tmp_path):
    store = OwnerSessionStore(tmp_path / "owner.sqlite3")

    missing = store.create()
    _set_last_seen(store, missing, None)
    assert store.valid(missing) is False
    assert _session_row(store, missing)["revoked_at"] is not None

    malformed = store.create()
    _set_last_seen(store, malformed, "not-a-timestamp")
    assert store.valid(malformed) is False
    assert _session_row(store, malformed)["revoked_at"] is not None
