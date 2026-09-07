from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore

router = APIRouter(tags=["Account Security"])
COOKIE_NAME = "lss_session"
CSRF_HEADER = "X-CSRF-Token"
CSRF_SCOPE = "destructive-member-actions"
CSRF_VERSION = "v1"
CSRF_TTL_SECONDS = 30 * 60
_MAX_CLOCK_SKEW_SECONDS = 60


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class SessionCsrfService:
    """Stateless HMAC CSRF tokens bound to one live member session.

    The token contains only a version, issue timestamp and signature. The signature binds the
    token to the SHA-256 digest of the current raw session token plus a fixed destructive-action
    scope. Revoking the underlying session therefore invalidates the CSRF token automatically.
    """

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS csrf_security_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    def _signing_key(self) -> bytes:
        configured = (os.getenv("LSS_CSRF_HMAC_KEY") or "").strip()
        if configured:
            # Hashing allows a deployment secret of arbitrary length while producing a fixed
            # 256-bit HMAC key. The configured secret itself is never stored in SQLite.
            return hashlib.sha256(configured.encode("utf-8")).digest()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT value FROM csrf_security_meta WHERE key='hmac_key'"
            ).fetchone()
            if not row:
                value = secrets.token_hex(32)
                con.execute(
                    "INSERT INTO csrf_security_meta (key,value,created_at) VALUES ('hmac_key',?,?)",
                    (value, _iso()),
                )
                row = con.execute(
                    "SELECT value FROM csrf_security_meta WHERE key='hmac_key'"
                ).fetchone()
        return bytes.fromhex(str(row["value"]))

    @staticmethod
    def _session_digest(session_token: str) -> str:
        return hashlib.sha256(session_token.encode("utf-8")).hexdigest()

    def _signature(self, session_token: str, issued_at: int) -> str:
        message = (
            f"{CSRF_VERSION}|{issued_at}|{self._session_digest(session_token)}|{CSRF_SCOPE}"
        ).encode("utf-8")
        return _b64url(hmac.new(self._signing_key(), message, hashlib.sha256).digest())

    def issue(self, session_token: str, *, now: int | None = None) -> dict:
        user = self.accounts.resolve_session(session_token)
        if not user:
            raise PermissionError("Sign in required")
        issued_at = int(time.time() if now is None else now)
        signature = self._signature(session_token, issued_at)
        return {
            "token": f"{CSRF_VERSION}.{issued_at}.{signature}",
            "expires_in_seconds": CSRF_TTL_SECONDS,
            "header": CSRF_HEADER,
            "scope": CSRF_SCOPE,
        }

    def verify(self, session_token: str, csrf_token: str, *, now: int | None = None) -> bool:
        if not session_token or not csrf_token:
            return False
        try:
            version, issued_text, supplied_signature = csrf_token.split(".", 2)
            issued_at = int(issued_text)
        except (TypeError, ValueError):
            return False
        if version != CSRF_VERSION:
            return False

        current = int(time.time() if now is None else now)
        age = current - issued_at
        if age < -_MAX_CLOCK_SKEW_SECONDS or age > CSRF_TTL_SECONDS:
            return False

        # Fail closed for revoked/expired/nonexistent sessions before accepting the HMAC.
        if not self.accounts.resolve_session(session_token):
            return False
        expected = self._signature(session_token, issued_at)
        return hmac.compare_digest(supplied_signature, expected)


service = SessionCsrfService()


@router.get("/auth/csrf-token")
def csrf_token(request: Request):
    # CSRF tokens are specifically for ambient cookie authentication. Bearer clients do not need
    # this browser protocol and should continue using Authorization on destructive API requests.
    session_token = request.cookies.get(COOKIE_NAME)
    if not session_token:
        raise HTTPException(401, "Cookie-authenticated member session required")
    try:
        issued = service.issue(session_token)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {
        "csrf_token": issued["token"],
        "expires_in_seconds": issued["expires_in_seconds"],
        "header": issued["header"],
        "scope": issued["scope"],
    }


__all__ = [
    "CSRF_HEADER",
    "CSRF_SCOPE",
    "CSRF_TTL_SECONDS",
    "SessionCsrfService",
    "router",
    "service",
]
