from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .accounts import (
    PASSWORD_SCHEME_ARGON2ID,
    AccountStore,
    _hash_password_argon2id,
    _hash_secret,
    _iso,
    _parse_iso,
    _utcnow,
)
from .audit import AuditLedger
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME
from .mailer import send_email

COOKIE_NAME = "lss_session"
RESET_TOKEN_MINUTES = 30
RESET_WINDOW_MINUTES = 60
RESET_MAX_REQUESTS_PER_WINDOW = 3
RESET_MIN_INTERVAL_SECONDS = 60
SECURITY_RECORD_RETENTION_DAYS = 7

router = APIRouter(tags=["Account Security"])


def _public_url() -> str:
    configured = (
        os.getenv("LSS_PUBLIC_BASE_URL")
        or os.getenv("LSS_PUBLIC_URL")
        or "http://127.0.0.1:8000"
    ).strip()
    return configured.rstrip("/")


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


@dataclass(frozen=True)
class PasswordResetIssue:
    user_id: str
    email: str
    display_name: str
    token: str
    expires_at: str


class AccountRecoveryStore:
    """Password recovery and session controls layered over the validated account store.

    Raw reset tokens are returned only to the caller that dispatches the email. SQLite stores
    SHA-256 token hashes. Password reset never changes membership, subscription or ESP roles.
    """

    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or AccountStore()
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        return self.accounts._connect()

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    invalidated_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_password_reset_user_created
                    ON password_reset_tokens(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS auth_password_reset_throttle (
                    identity_hash TEXT PRIMARY KEY,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL,
                    last_requested_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _identity_hash(email: str) -> str:
        normalized = (email or "").strip().lower()[:254]
        return _hash_secret("password-reset:" + normalized)

    def _consume_request_slot(self, email: str) -> bool:
        identity = self._identity_hash(email)
        now = _utcnow()
        retention_cutoff = _iso(now - timedelta(days=SECURITY_RECORD_RETENTION_DAYS))
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM auth_password_reset_throttle WHERE last_requested_at<?",
                (retention_cutoff,),
            )
            row = con.execute(
                """SELECT request_count,window_started_at,last_requested_at
                   FROM auth_password_reset_throttle WHERE identity_hash=?""",
                (identity,),
            ).fetchone()

            if not row:
                con.execute(
                    """INSERT INTO auth_password_reset_throttle
                       (identity_hash,request_count,window_started_at,last_requested_at)
                       VALUES (?,1,?,?)""",
                    (identity, _iso(now), _iso(now)),
                )
                return True

            window_started = _parse_iso(row["window_started_at"])
            last_requested = _parse_iso(row["last_requested_at"])
            if not window_started or now - window_started >= timedelta(minutes=RESET_WINDOW_MINUTES):
                con.execute(
                    """UPDATE auth_password_reset_throttle
                       SET request_count=1,window_started_at=?,last_requested_at=?
                       WHERE identity_hash=?""",
                    (_iso(now), _iso(now), identity),
                )
                return True

            if last_requested and (now - last_requested).total_seconds() < RESET_MIN_INTERVAL_SECONDS:
                return False
            if int(row["request_count"]) >= RESET_MAX_REQUESTS_PER_WINDOW:
                return False

            con.execute(
                """UPDATE auth_password_reset_throttle
                   SET request_count=request_count+1,last_requested_at=? WHERE identity_hash=?""",
                (_iso(now), identity),
            )
            return True

    def issue_password_reset(self, email: str) -> PasswordResetIssue | None:
        # Consume the same hashed-identity throttle slot for both known and unknown addresses.
        # The public route always returns the same response, so account existence is not exposed.
        if not self._consume_request_slot(email):
            return None

        user = self.accounts.get_user_by_email(email)
        if not user or user.get("disabled_at"):
            return None

        token = secrets.token_urlsafe(32)
        token_hash = _hash_secret(token)
        now = _utcnow()
        expires = now + timedelta(minutes=RESET_TOKEN_MINUTES)
        retention_cutoff = _iso(now - timedelta(days=SECURITY_RECORD_RETENTION_DAYS))

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at<?",
                (retention_cutoff,),
            )
            con.execute(
                """UPDATE password_reset_tokens SET invalidated_at=?
                   WHERE user_id=? AND used_at IS NULL AND invalidated_at IS NULL""",
                (_iso(now), user["id"]),
            )
            con.execute(
                """INSERT INTO password_reset_tokens
                   (id,user_id,token_hash,created_at,expires_at,used_at,invalidated_at)
                   VALUES (?,?,?,?,?,NULL,NULL)""",
                (uuid4().hex, user["id"], token_hash, _iso(now), _iso(expires)),
            )

        return PasswordResetIssue(
            user_id=str(user["id"]),
            email=str(user["email"]),
            display_name=str(user.get("display_name") or "Member"),
            token=token,
            expires_at=_iso(expires),
        )

    def reset_token_valid(self, token: str) -> bool:
        if not token:
            return False
        with self._connect() as con:
            row = con.execute(
                """SELECT expires_at FROM password_reset_tokens
                   WHERE token_hash=? AND used_at IS NULL AND invalidated_at IS NULL""",
                (_hash_secret(token),),
            ).fetchone()
        expires = _parse_iso(row["expires_at"]) if row else None
        return bool(expires and expires > _utcnow())

    def consume_password_reset(self, token: str, new_password: str) -> dict:
        digest = _hash_password_argon2id(new_password)
        token_hash = _hash_secret(token or "")
        now = _utcnow()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT pr.id,pr.user_id,pr.expires_at,u.email
                   FROM password_reset_tokens pr
                   JOIN users u ON u.id=pr.user_id
                   WHERE pr.token_hash=? AND pr.used_at IS NULL AND pr.invalidated_at IS NULL""",
                (token_hash,),
            ).fetchone()
            if not row:
                raise ValueError("Password reset link is invalid or has already been used")

            expires = _parse_iso(row["expires_at"])
            if not expires or expires <= now:
                raise ValueError("Password reset link has expired")

            con.execute(
                """UPDATE users
                   SET password_salt='',password_hash=?,password_scheme=?,password_updated_at=?
                   WHERE id=?""",
                (digest, PASSWORD_SCHEME_ARGON2ID, _iso(now), row["user_id"]),
            )
            con.execute(
                "UPDATE password_reset_tokens SET used_at=? WHERE id=?",
                (_iso(now), row["id"]),
            )
            con.execute(
                """UPDATE password_reset_tokens SET invalidated_at=?
                   WHERE user_id=? AND id<>? AND used_at IS NULL AND invalidated_at IS NULL""",
                (_iso(now), row["user_id"], row["id"]),
            )
            con.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (_iso(now), row["user_id"]),
            )

        self.accounts.clear_login_throttle(str(row["email"]))
        return {"user_id": str(row["user_id"]), "sessions_revoked": True}

    def current_session_id(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._connect() as con:
            row = con.execute(
                """SELECT id FROM sessions
                   WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?""",
                (_hash_secret(token), _iso()),
            ).fetchone()
        return str(row["id"]) if row else None

    def list_sessions(self, user_id: str, current_token: str) -> list[dict]:
        current_id = self.current_session_id(current_token)
        if not current_id:
            raise PermissionError("Active session required")
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,created_at,expires_at FROM sessions
                   WHERE user_id=? AND revoked_at IS NULL AND expires_at>?
                   ORDER BY created_at DESC,id DESC""",
                (user_id, _iso()),
            ).fetchall()
        return [
            {
                "session_id": str(row["id"]),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "current": str(row["id"]) == current_id,
                "active": True,
            }
            for row in rows
        ]

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        now = _iso()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT id FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL",
                (session_id, user_id),
            ).fetchone()
            if not row:
                return False
            con.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (now, session_id))
        return True

    def revoke_other_sessions(self, user_id: str, current_token: str) -> int:
        current_id = self.current_session_id(current_token)
        if not current_id:
            raise PermissionError("Active session required")
        now = _iso()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            cursor = con.execute(
                """UPDATE sessions SET revoked_at=?
                   WHERE user_id=? AND id<>? AND revoked_at IS NULL AND expires_at>?""",
                (now, user_id, current_id, now),
            )
            return int(cursor.rowcount or 0)

    def change_password(
        self,
        user_id: str,
        current_token: str,
        current_password: str,
        new_password: str,
    ) -> int:
        if current_password == new_password:
            raise ValueError("New password must be different from the current password")

        user = self.accounts.get_user(user_id)
        if not user:
            raise PermissionError("Account not found")
        authenticated = self.accounts.authenticate(str(user["email"]), current_password)
        if not authenticated or authenticated["id"] != user_id:
            raise PermissionError("Current password is incorrect")

        current_id = self.current_session_id(current_token)
        if not current_id:
            raise PermissionError("Active session required")
        digest = _hash_password_argon2id(new_password)
        now = _iso()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """UPDATE users
                   SET password_salt='',password_hash=?,password_scheme=?,password_updated_at=?
                   WHERE id=?""",
                (digest, PASSWORD_SCHEME_ARGON2ID, now, user_id),
            )
            cursor = con.execute(
                """UPDATE sessions SET revoked_at=?
                   WHERE user_id=? AND id<>? AND revoked_at IS NULL AND expires_at>?""",
                (now, user_id, current_id, now),
            )
            con.execute(
                """UPDATE password_reset_tokens SET invalidated_at=?
                   WHERE user_id=? AND used_at IS NULL AND invalidated_at IS NULL""",
                (now, user_id),
            )
        return int(cursor.rowcount or 0)


account_store = AccountStore()
store = AccountRecoveryStore(account_store)
audit = AuditLedger(account_store)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str = Field(min_length=10, max_length=512)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=10, max_length=512)


def _session_context(request: Request) -> tuple[dict, str, str]:
    token = _session_token(request)
    user = account_store.resolve_session(token)
    if not user or not token:
        raise HTTPException(401, "Sign in required")
    session_id = store.current_session_id(token)
    if not session_id:
        raise HTTPException(401, "Active session required")
    return user, token, session_id


def _send_reset_email(issue: PasswordResetIssue) -> None:
    link = f"{_public_url()}/auth/password-reset?token={quote(issue.token, safe='')}"
    subject = f"Reset your {PRODUCT_FULL_NAME} password"
    body = f"""Hello {issue.display_name},

A password reset was requested for your {PRODUCT_FULL_NAME} account.

Reset your password here:
{link}

This link is single-use and expires in {RESET_TOKEN_MINUTES} minutes. If you did not request this, you can ignore this message; your current password remains unchanged.

{ENDORSEMENT}
"""
    send_email(issue.email, subject, body)


@router.post("/auth/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, background_tasks: BackgroundTasks):
    issue = store.issue_password_reset(payload.email)
    if issue:
        audit.append(
            actor="member-recovery",
            action="password_reset_requested",
            subject_user_id=issue.user_id,
            details={"expires_at": issue.expires_at},
        )
        background_tasks.add_task(_send_reset_email, issue)
    return {
        "accepted": True,
        "message": "If an eligible account matches that email, a password reset link will be sent.",
    }


@router.get("/auth/password-reset", response_class=HTMLResponse)
def password_reset_page(token: str):
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if not store.reset_token_valid(token):
        return HTMLResponse(
            "<h2>Password reset link is invalid or expired.</h2>",
            status_code=410,
            headers=headers,
        )
    safe_token = escape(token, quote=True)
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'>
<title>Password Reset — {escape(PRODUCT_FULL_NAME)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0d0715;color:#fff;margin:0;padding:30px}}
.card{{max-width:560px;margin:60px auto;padding:30px;border-radius:24px;background:#1d1230}}
input{{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #5f4a72;background:#120b1e;color:#fff;margin:8px 0 18px}}
button{{padding:14px 22px;border:0;border-radius:12px;font-weight:800;cursor:pointer}}
</style></head><body><div class='card'>
<h1>{escape(PRODUCT_FULL_NAME)}</h1><h2>Choose a new password</h2>
<form method='post' action='/auth/password-reset/confirm-form'>
<input type='hidden' name='token' value='{safe_token}'>
<label>New password</label><input type='password' name='new_password' minlength='10' maxlength='512' required>
<label>Confirm new password</label><input type='password' name='confirm_password' minlength='10' maxlength='512' required>
<button type='submit'>Reset password</button>
</form></div></body></html>""",
        headers=headers,
    )


@router.post("/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, response: Response):
    try:
        result = store.consume_password_reset(payload.token, payload.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.append(
        actor="member-recovery",
        action="password_reset_completed",
        subject_user_id=result["user_id"],
        details={"all_previous_sessions_revoked": True},
    )
    response.delete_cookie(COOKIE_NAME)
    response.headers["Cache-Control"] = "no-store"
    return {
        "reset": True,
        "all_previous_sessions_revoked": True,
        "message": "Password updated. Sign in again on each device you want to keep using.",
    }


@router.post("/auth/password-reset/confirm-form", response_class=HTMLResponse)
def confirm_password_reset_form(
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if new_password != confirm_password:
        return HTMLResponse("<h2>Passwords did not match.</h2>", status_code=400, headers=headers)
    try:
        result = store.consume_password_reset(token, new_password)
    except ValueError as exc:
        return HTMLResponse(f"<h2>{escape(str(exc))}</h2>", status_code=400, headers=headers)
    audit.append(
        actor="member-recovery",
        action="password_reset_completed",
        subject_user_id=result["user_id"],
        details={"all_previous_sessions_revoked": True},
    )
    response = HTMLResponse(
        "<h2>Password updated.</h2><p>All previous sessions were signed out. You can now sign in with your new password.</p>",
        headers=headers,
    )
    response.delete_cookie(COOKIE_NAME)
    return response


@router.post("/auth/password/change")
def change_password(payload: PasswordChangeRequest, request: Request):
    user, token, _ = _session_context(request)
    try:
        revoked = store.change_password(
            str(user["id"]),
            token,
            payload.current_password,
            payload.new_password,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.append(
        actor="member-self-service",
        action="password_changed",
        subject_user_id=str(user["id"]),
        details={"other_sessions_revoked": revoked},
    )
    return {
        "changed": True,
        "current_session_preserved": True,
        "other_sessions_revoked": revoked,
    }


@router.get("/auth/sessions")
def active_sessions(request: Request):
    user, token, _ = _session_context(request)
    try:
        sessions = store.list_sessions(str(user["id"]), token)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {
        "sessions": sessions,
        "active_count": len(sessions),
        "device_details_available": False,
    }


@router.delete("/auth/sessions/{session_id}")
def revoke_session(session_id: str, request: Request, response: Response):
    user, _, current_id = _session_context(request)
    if not store.revoke_session(str(user["id"]), session_id):
        raise HTTPException(404, "Session not found")
    current_revoked = session_id == current_id
    if current_revoked:
        response.delete_cookie(COOKIE_NAME)
    audit.append(
        actor="member-self-service",
        action="session_revoked",
        subject_user_id=str(user["id"]),
        details={"session_id": session_id, "current_session": current_revoked},
    )
    return {"revoked": True, "current_session": current_revoked}


@router.post("/auth/sessions/revoke-others")
def revoke_other_sessions(request: Request):
    user, token, _ = _session_context(request)
    try:
        count = store.revoke_other_sessions(str(user["id"]), token)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    audit.append(
        actor="member-self-service",
        action="other_sessions_revoked",
        subject_user_id=str(user["id"]),
        details={"revoked_count": count},
    )
    return {"revoked": count, "current_session_preserved": True}


__all__ = [
    "AccountRecoveryStore",
    "PasswordResetIssue",
    "router",
]
