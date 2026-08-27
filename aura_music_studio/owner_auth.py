from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from starlette.responses import Response

OWNER_COOKIE = "lss_admin_session"
OWNER_SESSION_HOURS = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _db_path() -> Path:
    path = Path(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class OwnerSessionStore:
    """Server-side owner sessions with only a random bearer token in the browser.

    The deployment owner key is used only to bootstrap a login. The browser never retains
    that deployment secret after authentication; only the SHA-256 hash of the random
    session token is stored in SQLite.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else _db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_owner_session_hash
                    ON owner_sessions(token_hash);
                """
            )

    def create(self) -> str:
        token = secrets.token_urlsafe(48)
        now = _now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO owner_sessions(id,token_hash,created_at,expires_at,last_seen_at) VALUES (?,?,?,?,?)",
                (
                    uuid4().hex,
                    _hash(token),
                    _iso(now),
                    _iso(now + timedelta(hours=OWNER_SESSION_HOURS)),
                    _iso(now),
                ),
            )
            con.execute(
                "DELETE FROM owner_sessions WHERE expires_at<? OR (revoked_at IS NOT NULL AND revoked_at<?)",
                (_iso(now - timedelta(days=1)), _iso(now - timedelta(days=30))),
            )
        return token

    def valid(self, token: str | None, *, touch: bool = True) -> bool:
        if not token:
            return False
        now = _iso()
        with self._connect() as con:
            row = con.execute(
                """SELECT id FROM owner_sessions
                   WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?""",
                (_hash(token), now),
            ).fetchone()
            if not row:
                return False
            if touch:
                con.execute("UPDATE owner_sessions SET last_seen_at=? WHERE id=?", (now, row["id"]))
        return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as con:
            con.execute(
                "UPDATE owner_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (_iso(), _hash(token)),
            )


_sessions: OwnerSessionStore | None = None


def sessions() -> OwnerSessionStore:
    global _sessions
    desired = _db_path().resolve()
    if _sessions is None or _sessions.db_path.resolve() != desired:
        _sessions = OwnerSessionStore(desired)
    return _sessions


def owner_key_matches(candidate: str) -> bool:
    """Verify the deployment bootstrap credential without turning it into a session."""

    configured = (os.getenv("LSS_ADMIN_KEY") or "").strip()
    return bool(configured and candidate and hmac.compare_digest(configured, candidate))


def owner_authorized(request: Request) -> bool:
    """Authorize only a live random opaque owner session."""

    return sessions().valid(request.cookies.get(OWNER_COOKIE))


def start_owner_session(response: Response) -> str:
    token = sessions().create()
    response.set_cookie(
        OWNER_COOKIE,
        token,
        max_age=OWNER_SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="strict",
    )
    return token


def end_owner_session(request: Request, response: Response) -> None:
    token = request.cookies.get(OWNER_COOKIE)
    sessions().revoke(token)
    response.delete_cookie(OWNER_COOKIE)
