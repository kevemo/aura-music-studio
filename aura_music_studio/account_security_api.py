from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .accounts import (
    AccountStore,
    PASSWORD_SCHEME_ARGON2ID,
    _hash_password_argon2id,
    _hash_secret,
)
from .audit import AuditLedger
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .mailer import _public_url, send_email

router = APIRouter(tags=["Account Security"])
COOKIE_NAME = "lss_session"
RESET_TTL_MINUTES = 30
RESET_REQUEST_WINDOW_MINUTES = 30
RESET_REQUEST_LIMIT = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _identity_hash(email: str) -> str:
    normalized = (email or "").strip().lower()[:254]
    return hashlib.sha256(("password-reset:" + normalized).encode("utf-8")).hexdigest()


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str = Field(min_length=10, max_length=512)


class AccountSecurityService:
    """Recovery/session security backed by the canonical AccountStore database."""

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()
        self.audit = AuditLedger(self.accounts)
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

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
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_password_reset_user_created
                    ON password_reset_tokens(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS password_reset_throttle (
                    identity_hash TEXT PRIMARY KEY,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL,
                    last_requested_at TEXT NOT NULL
                );
                """
            )

    def _request_allowed(self, email: str) -> bool:
        identity = _identity_hash(email)
        now = _utcnow()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT request_count,window_started_at FROM password_reset_throttle WHERE identity_hash=?",
                (identity,),
            ).fetchone()
            window_start = _parse_iso(row["window_started_at"]) if row else None
            if not window_start or now - window_start > timedelta(minutes=RESET_REQUEST_WINDOW_MINUTES):
                count = 1
                window_start = now
            else:
                count = int(row["request_count"]) + 1
            con.execute(
                """INSERT INTO password_reset_throttle
                   (identity_hash,request_count,window_started_at,last_requested_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(identity_hash) DO UPDATE SET
                     request_count=excluded.request_count,
                     window_started_at=excluded.window_started_at,
                     last_requested_at=excluded.last_requested_at""",
                (identity, count, _iso(window_start), _iso(now)),
            )
        return count <= RESET_REQUEST_LIMIT

    def create_password_reset(self, email: str) -> dict:
        allowed = self._request_allowed(email)
        user = self.accounts.get_user_by_email(email)
        if not allowed or not user or user.get("disabled_at") or user.get("status") == "rejected":
            return {"issued": False, "reason": "generic"}

        token = secrets.token_urlsafe(40)
        now = _utcnow()
        expires = now + timedelta(minutes=RESET_TTL_MINUTES)
        with self._connect() as con:
            con.execute(
                "UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (_iso(now), user["id"]),
            )
            con.execute(
                """INSERT INTO password_reset_tokens
                   (id,user_id,token_hash,created_at,expires_at,used_at)
                   VALUES (?,?,?,?,?,NULL)""",
                (uuid4().hex, user["id"], _hash_secret(token), _iso(now), _iso(expires)),
            )
            con.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at<? AND used_at IS NOT NULL",
                (_iso(now - timedelta(days=7)),),
            )
        return {
            "issued": True,
            "token": token,
            "user_id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "expires_at": _iso(expires),
        }

    def complete_password_reset(self, token: str, new_password: str) -> dict:
        digest = _hash_password_argon2id(new_password)
        token_hash = _hash_secret(token)
        now = _utcnow()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT pr.*,u.email FROM password_reset_tokens pr
                   JOIN users u ON u.id=pr.user_id WHERE pr.token_hash=?""",
                (token_hash,),
            ).fetchone()
            expires = _parse_iso(row["expires_at"]) if row else None
            if not row or row["used_at"] or not expires or expires <= now:
                raise ValueError("Password reset link is invalid or expired")

            con.execute(
                """UPDATE users SET password_salt='',password_hash=?,password_scheme=?,password_updated_at=?
                   WHERE id=?""",
                (digest, PASSWORD_SCHEME_ARGON2ID, _iso(now), row["user_id"]),
            )
            con.execute(
                "UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (_iso(now), row["user_id"]),
            )
            con.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (_iso(now), row["user_id"]),
            )
            user_id = str(row["user_id"])
            email = str(row["email"])

        self.accounts.clear_login_throttle(email)
        self.audit.append(
            actor="member-security",
            action="password_reset_completed",
            subject_user_id=user_id,
            details={"all_existing_sessions_revoked": True},
        )
        return {"reset": True, "user_id": user_id, "sessions_revoked": True}

    def list_sessions(self, current_token: str) -> list[dict]:
        user = self.accounts.resolve_session(current_token)
        if not user:
            raise PermissionError("Sign in required")
        current_hash = _hash_secret(current_token)
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,token_hash,created_at,expires_at,revoked_at FROM sessions
                   WHERE user_id=? AND expires_at>? ORDER BY created_at DESC""",
                (user["id"], _iso()),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "revoked_at": row["revoked_at"],
                "active": row["revoked_at"] is None,
                "current": secrets.compare_digest(str(row["token_hash"]), current_hash),
            }
            for row in rows
        ]

    def revoke_session_id(self, current_token: str, session_id: str) -> dict:
        user = self.accounts.resolve_session(current_token)
        if not user:
            raise PermissionError("Sign in required")
        current_hash = _hash_secret(current_token)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT id,token_hash,revoked_at FROM sessions WHERE id=? AND user_id=?",
                (session_id, user["id"]),
            ).fetchone()
            if not row:
                raise ValueError("Session not found")
            is_current = secrets.compare_digest(str(row["token_hash"]), current_hash)
            if row["revoked_at"] is None:
                con.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (_iso(), session_id))
        self.audit.append(
            actor="member-security",
            action="session_revoked",
            subject_user_id=user["id"],
            details={"session_id": session_id, "current_session": is_current},
        )
        return {"revoked": True, "session_id": session_id, "current": is_current}

    def revoke_other_sessions(self, current_token: str) -> int:
        user = self.accounts.resolve_session(current_token)
        if not user:
            raise PermissionError("Sign in required")
        current_hash = _hash_secret(current_token)
        with self._connect() as con:
            cursor = con.execute(
                """UPDATE sessions SET revoked_at=?
                   WHERE user_id=? AND token_hash<>? AND revoked_at IS NULL AND expires_at>?""",
                (_iso(), user["id"], current_hash, _iso()),
            )
            count = int(cursor.rowcount or 0)
        self.audit.append(
            actor="member-security",
            action="other_sessions_revoked",
            subject_user_id=user["id"],
            details={"revoked_count": count},
        )
        return count


service = AccountSecurityService()


def _deliver_reset(email: str, display_name: str, token: str) -> None:
    # The reset secret lives in the fragment, which browsers do not send in the HTTP request.
    # This keeps it out of access logs and server-side URL processing.
    reset_url = f"{_public_url()}/auth/reset-password#token={quote(token)}"
    subject = f"Reset your {PRODUCT_FULL_NAME} password"
    body = (
        f"Hello {display_name},\n\n"
        "A password reset was requested for your account.\n\n"
        f"Reset your password within {RESET_TTL_MINUTES} minutes:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this message.\n\n"
        f"{ENDORSEMENT}\n"
    )
    try:
        send_email(email, subject, body)
    except Exception:
        pass


@router.post("/auth/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, background_tasks: BackgroundTasks):
    issued = service.create_password_reset(payload.email)
    if issued.get("issued"):
        background_tasks.add_task(
            _deliver_reset,
            str(issued["email"]),
            str(issued["display_name"]),
            str(issued["token"]),
        )
    return {
        "accepted": True,
        "message": "If an eligible account exists for that email, password-reset instructions will be sent.",
    }


@router.post("/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, response: Response):
    try:
        result = service.complete_password_reset(payload.token, payload.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response.delete_cookie(COOKIE_NAME)
    return {
        "reset": result["reset"],
        "sessions_revoked": True,
        "message": "Password changed. Sign in again on your trusted devices.",
    }


@router.get("/auth/sessions")
def sessions(request: Request):
    token = _session_token(request)
    try:
        items = service.list_sessions(token or "")
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"sessions": items, "raw_tokens_exposed": False}


@router.delete("/auth/sessions/{session_id}")
def revoke_session(session_id: str, request: Request, response: Response):
    token = _session_token(request)
    try:
        result = service.revoke_session_id(token or "", session_id)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if result["current"]:
        response.delete_cookie(COOKIE_NAME)
    return result


@router.post("/auth/sessions/revoke-others")
def revoke_other_sessions(request: Request):
    token = _session_token(request)
    try:
        count = service.revoke_other_sessions(token or "")
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"revoked": count, "current_session_preserved": True}


def _security_page(title: str, inner: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(title)} — {escape(PRODUCT_FULL_NAME)}</title><style>
body{{font-family:system-ui,sans-serif;background:#0d0715;color:#fff;margin:0;padding:28px}}
.card{{max-width:620px;margin:60px auto;padding:28px;border-radius:24px;background:#1d1230;box-shadow:0 20px 60px #0008}}
.muted{{color:#c9bfd5}} input,button{{width:100%;box-sizing:border-box;padding:13px;border-radius:11px;margin:8px 0}}
input{{border:1px solid #665378;background:#120b1e;color:#fff}} button{{border:0;background:#f2c35c;color:#1a1024;font-weight:800;cursor:pointer}}
a{{color:#f2c35c}}
</style></head><body><div class='card'><h1>{escape(PRODUCT_FULL_NAME)}</h1><p class='muted'>{escape(TAGLINE)}</p>{inner}</div></body></html>"""
    )


@router.get("/auth/forgot-password", response_class=HTMLResponse)
def forgot_password_page():
    return _security_page(
        "Forgot password",
        """<h2>Reset your password</h2><p class='muted'>Enter the email on your account. For privacy, the response is the same whether or not an eligible account exists.</p>
<input id='email' type='email' maxlength='254' autocomplete='email' placeholder='Email address'>
<button id='submit'>Send reset instructions</button><p id='status' class='muted'></p><p><a href='/signin'>Back to sign in</a></p>
<script>
const status=document.getElementById('status');
document.getElementById('submit').onclick=async()=>{
 const email=document.getElementById('email').value;
 status.textContent='Submitting…';
 try{
  const r=await fetch('/auth/password-reset/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
  const data=await r.json(); status.textContent=data.message || 'If the account is eligible, instructions will be sent.';
 }catch(e){status.textContent='Could not submit the request. Please try again.';}
};
</script>""",
    )


@router.get("/auth/reset-password", response_class=HTMLResponse)
def reset_password_page():
    return _security_page(
        "Password reset",
        """<h2>Choose a new password</h2>
<input id='password' type='password' minlength='10' maxlength='512' autocomplete='new-password' placeholder='New password (10+ characters)'>
<button id='submit'>Reset password</button><p id='status' class='muted'></p>
<script>
const token=new URLSearchParams(window.location.hash.slice(1)).get('token') || '';
try{history.replaceState({},'', '/auth/reset-password')}catch(e){}
const status=document.getElementById('status');
document.getElementById('submit').onclick=async()=>{
 const password=document.getElementById('password').value; status.textContent='Updating…';
 try{
  const r=await fetch('/auth/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,new_password:password})});
  const data=await r.json(); status.textContent=r.ok ? data.message : (data.detail || 'Reset failed.');
 }catch(e){status.textContent='Reset failed. Please try again.';}
};
</script>""",
    )


__all__ = ["router", "AccountSecurityService"]
