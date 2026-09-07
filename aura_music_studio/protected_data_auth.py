from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Request
from starlette.responses import Response

from .owner_auth import owner_authorized
from .owner_identity import request_owner_persona

ProtectedPersona = Literal["mary", "kev"]
PROTECTED_DATA_COOKIE = "shared_skies_protected_session"
PROTECTED_SESSION_MINUTES = 15
_PROTECTED_KEYS: dict[ProtectedPersona, str] = {
    "mary": "SHARED_SKIES_PROTECTED_MARY_KEY",
    "kev": "SHARED_SKIES_PROTECTED_KEV_KEY",
}


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


def protected_key_configured(persona: ProtectedPersona) -> bool:
    env_name = _PROTECTED_KEYS[persona]
    return bool((os.getenv(env_name) or "").strip())


def protected_key_matches(persona: ProtectedPersona, candidate: str) -> bool:
    configured = (os.getenv(_PROTECTED_KEYS[persona]) or "").strip()
    supplied = (candidate or "").strip()
    return bool(configured and supplied and hmac.compare_digest(configured, supplied))


class ProtectedDataSessionStore:
    """Independent short-lived step-up sessions for protected owner resources.

    Ordinary Owner authentication is necessary but never sufficient for Protected Data.
    Only hashes of random browser bearer tokens are persisted. Mary and Kev use distinct
    deployment-managed step-up secrets; there are no permissive defaults.
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
                CREATE TABLE IF NOT EXISTS protected_data_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    persona TEXT NOT NULL CHECK(persona IN ('mary','kev')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_protected_data_session_hash
                    ON protected_data_sessions(token_hash);

                CREATE TABLE IF NOT EXISTS protected_data_audit_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    persona TEXT,
                    action TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_protected_data_audit_created
                    ON protected_data_audit_events(created_at);
                """
            )

    def _audit_in_transaction(
        self,
        con: sqlite3.Connection,
        event_type: str,
        *,
        persona: ProtectedPersona | None = None,
        action: str | None = None,
    ) -> None:
        event = (event_type or "").strip()[:80]
        action_value = (action or "").strip()[:160] or None
        if not event:
            raise ValueError("Protected Data audit event type is required")
        con.execute(
            "INSERT INTO protected_data_audit_events(id,event_type,persona,action,created_at) VALUES (?,?,?,?,?)",
            (uuid4().hex, event, persona, action_value, _iso()),
        )

    def audit(
        self,
        event_type: str,
        *,
        persona: ProtectedPersona | None = None,
        action: str | None = None,
    ) -> None:
        with self._connect() as con:
            self._audit_in_transaction(con, event_type, persona=persona, action=action)

    def create(self, persona: ProtectedPersona) -> str:
        if persona not in _PROTECTED_KEYS:
            raise ValueError("Unknown protected persona")
        token = secrets.token_urlsafe(48)
        now = _now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO protected_data_sessions(id,token_hash,persona,created_at,expires_at,last_seen_at) VALUES (?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    _hash(token),
                    persona,
                    _iso(now),
                    _iso(now + timedelta(minutes=PROTECTED_SESSION_MINUTES)),
                    _iso(now),
                ),
            )
            self._audit_in_transaction(con, "step_up_accepted", persona=persona)
            con.execute(
                "DELETE FROM protected_data_sessions WHERE expires_at<? OR (revoked_at IS NOT NULL AND revoked_at<?)",
                (_iso(now - timedelta(days=1)), _iso(now - timedelta(days=30))),
            )
        return token

    def resolve(self, token: str | None, *, touch: bool = True) -> ProtectedPersona | None:
        if not token:
            return None
        now = _iso()
        with self._connect() as con:
            row = con.execute(
                """SELECT id,persona FROM protected_data_sessions
                   WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?""",
                (_hash(token), now),
            ).fetchone()
            if not row:
                return None
            if touch:
                con.execute("UPDATE protected_data_sessions SET last_seen_at=? WHERE id=?", (now, row["id"]))
        return str(row["persona"])  # type: ignore[return-value]

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        token_hash = _hash(token)
        with self._connect() as con:
            row = con.execute(
                "SELECT persona FROM protected_data_sessions WHERE token_hash=? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
            if not row:
                return
            persona = str(row["persona"])
            con.execute(
                "UPDATE protected_data_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (_iso(), token_hash),
            )
            self._audit_in_transaction(con, "step_up_revoked", persona=persona)  # type: ignore[arg-type]

    def revoke_all(self, persona: ProtectedPersona | None = None) -> int:
        now = _iso()
        with self._connect() as con:
            if persona:
                cursor = con.execute(
                    "UPDATE protected_data_sessions SET revoked_at=? WHERE persona=? AND revoked_at IS NULL",
                    (now, persona),
                )
            else:
                cursor = con.execute(
                    "UPDATE protected_data_sessions SET revoked_at=? WHERE revoked_at IS NULL",
                    (now,),
                )
            self._audit_in_transaction(con, "emergency_revoke_all", persona=persona)
            return int(cursor.rowcount)


_store: ProtectedDataSessionStore | None = None


def protected_sessions() -> ProtectedDataSessionStore:
    global _store
    desired = _db_path().resolve()
    if _store is None or _store.db_path.resolve() != desired:
        _store = ProtectedDataSessionStore(desired)
    return _store


def protected_data_authorized(request: Request) -> bool:
    """Require both a live Owner session and a persona-bound Protected Data step-up."""

    if not owner_authorized(request):
        return False
    persona = request_owner_persona(request)
    if persona not in _PROTECTED_KEYS:
        return False
    resolved = protected_sessions().resolve(request.cookies.get(PROTECTED_DATA_COOKIE))
    return bool(resolved and hmac.compare_digest(resolved, persona))


def authorize_protected_action(request: Request, action: str) -> ProtectedPersona | None:
    """Authorize and audit a consequential Protected Data action.

    Audit persistence is part of admission. Database/audit failures are deliberately not
    swallowed, so consequential protected operations fail closed if evidence cannot be written.
    """

    if not protected_data_authorized(request):
        return None
    persona = request_owner_persona(request)
    if persona not in _PROTECTED_KEYS:
        return None
    protected_sessions().audit("protected_action_authorized", persona=persona, action=action)
    return persona


def record_step_up_denial(persona: ProtectedPersona | None) -> None:
    protected_sessions().audit("step_up_denied", persona=persona)


def start_protected_session(response: Response, persona: ProtectedPersona) -> str:
    token = protected_sessions().create(persona)
    response.set_cookie(
        PROTECTED_DATA_COOKIE,
        token,
        max_age=PROTECTED_SESSION_MINUTES * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="strict",
    )
    return token


def end_protected_session(request: Request, response: Response) -> None:
    protected_sessions().revoke(request.cookies.get(PROTECTED_DATA_COOKIE))
    response.delete_cookie(PROTECTED_DATA_COOKIE)


__all__ = [
    "PROTECTED_DATA_COOKIE",
    "PROTECTED_SESSION_MINUTES",
    "ProtectedDataSessionStore",
    "ProtectedPersona",
    "authorize_protected_action",
    "end_protected_session",
    "protected_data_authorized",
    "protected_key_configured",
    "protected_key_matches",
    "protected_sessions",
    "record_step_up_denial",
    "start_protected_session",
]
