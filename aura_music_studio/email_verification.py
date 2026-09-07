from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore, _hash_secret
from .audit import AuditLedger
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .mailer import _public_url, send_email

router = APIRouter(tags=["Account Security"])
VERIFY_TTL_HOURS = 24
VERIFY_REQUEST_WINDOW_MINUTES = 30
VERIFY_REQUEST_LIMIT = 3


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
    return hashlib.sha256(("email-verification:" + normalized).encode("utf-8")).hexdigest()


class EmailVerificationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class EmailVerificationService:
    """Verified-email state for new memberships without locking out legacy accounts.

    The service records a rollout timestamp on first initialization. Accounts created before
    that timestamp are grandfathered when they have no explicit verification-state row.
    Accounts created after rollout fail closed unless they are explicitly enrolled and verified.
    """

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
        now = _iso()
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_verification_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS email_verification_state (
                    user_id TEXT PRIMARY KEY,
                    enrolled_at TEXT NOT NULL,
                    verified_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS email_verification_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_email_verification_user_created
                    ON email_verification_tokens(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS email_verification_throttle (
                    identity_hash TEXT PRIMARY KEY,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL,
                    last_requested_at TEXT NOT NULL
                );
                """
            )
            con.execute(
                "INSERT INTO email_verification_meta (key,value) VALUES ('rollout_at',?) "
                "ON CONFLICT(key) DO NOTHING",
                (now,),
            )

    def rollout_at(self) -> datetime:
        with self._connect() as con:
            row = con.execute(
                "SELECT value FROM email_verification_meta WHERE key='rollout_at'"
            ).fetchone()
        parsed = _parse_iso(row["value"] if row else None)
        return parsed or _utcnow()

    def register_new_user(self, user_id: str) -> None:
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        with self._connect() as con:
            con.execute(
                """INSERT INTO email_verification_state (user_id,enrolled_at,verified_at)
                   VALUES (?,?,NULL)
                   ON CONFLICT(user_id) DO NOTHING""",
                (user_id, _iso()),
            )

    def is_verified(self, user_id: str) -> bool:
        user = self.accounts.get_user(user_id)
        if not user:
            return False
        with self._connect() as con:
            row = con.execute(
                "SELECT verified_at FROM email_verification_state WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if row:
            return bool(row["verified_at"])

        # No explicit state means legacy only when the account truly predates rollout.
        created_at = _parse_iso(user.get("created_at"))
        return bool(created_at and created_at < self.rollout_at())

    def _request_allowed(self, email: str) -> bool:
        identity = _identity_hash(email)
        now = _utcnow()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT request_count,window_started_at FROM email_verification_throttle "
                "WHERE identity_hash=?",
                (identity,),
            ).fetchone()
            window_start = _parse_iso(row["window_started_at"]) if row else None
            if not window_start or now - window_start > timedelta(minutes=VERIFY_REQUEST_WINDOW_MINUTES):
                count = 1
                window_start = now
            else:
                count = int(row["request_count"]) + 1
            con.execute(
                """INSERT INTO email_verification_throttle
                   (identity_hash,request_count,window_started_at,last_requested_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(identity_hash) DO UPDATE SET
                     request_count=excluded.request_count,
                     window_started_at=excluded.window_started_at,
                     last_requested_at=excluded.last_requested_at""",
                (identity, count, _iso(window_start), _iso(now)),
            )
        return count <= VERIFY_REQUEST_LIMIT

    def issue_for_user(self, user_id: str) -> dict:
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        if self.is_verified(user_id):
            return {"issued": False, "verified": True}

        token = secrets.token_urlsafe(40)
        now = _utcnow()
        expires = now + timedelta(hours=VERIFY_TTL_HOURS)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE email_verification_tokens SET used_at=? "
                "WHERE user_id=? AND used_at IS NULL",
                (_iso(now), user_id),
            )
            con.execute(
                """INSERT INTO email_verification_tokens
                   (id,user_id,token_hash,created_at,expires_at,used_at)
                   VALUES (?,?,?,?,?,NULL)""",
                (uuid4().hex, user_id, _hash_secret(token), _iso(now), _iso(expires)),
            )
            con.execute(
                "DELETE FROM email_verification_tokens WHERE expires_at<? AND used_at IS NOT NULL",
                (_iso(now - timedelta(days=7)),),
            )
        return {
            "issued": True,
            "token": token,
            "user_id": user_id,
            "email": user["email"],
            "display_name": user["display_name"],
            "expires_at": _iso(expires),
        }

    def request_for_email(self, email: str) -> dict:
        allowed = self._request_allowed(email)
        user = self.accounts.get_user_by_email(email)
        if not allowed or not user or user.get("disabled_at") or user.get("status") == "rejected":
            return {"issued": False, "reason": "generic"}
        if self.is_verified(user["id"]):
            return {"issued": False, "reason": "generic"}
        return self.issue_for_user(user["id"])

    def complete(self, token: str) -> dict:
        token_hash = _hash_secret(token)
        now = _utcnow()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT ev.*,u.email FROM email_verification_tokens ev
                   JOIN users u ON u.id=ev.user_id WHERE ev.token_hash=?""",
                (token_hash,),
            ).fetchone()
            expires = _parse_iso(row["expires_at"]) if row else None
            if not row or row["used_at"] or not expires or expires <= now:
                raise ValueError("Email verification link is invalid or expired")

            con.execute(
                """INSERT INTO email_verification_state (user_id,enrolled_at,verified_at)
                   VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET verified_at=excluded.verified_at""",
                (row["user_id"], row["created_at"], _iso(now)),
            )
            con.execute(
                "UPDATE email_verification_tokens SET used_at=? "
                "WHERE user_id=? AND used_at IS NULL",
                (_iso(now), row["user_id"]),
            )
            user_id = str(row["user_id"])

        self.audit.append(
            actor="member-security",
            action="email_verified",
            subject_user_id=user_id,
        )
        return {"verified": True, "user_id": user_id}


service = EmailVerificationService()


def deliver_email_verification(email: str, display_name: str, token: str) -> dict:
    # The secret is held in the fragment; browsers never include fragments in the HTTP request URL.
    verify_url = f"{_public_url()}/auth/verify-email#token={quote(token)}"
    subject = f"Verify your {PRODUCT_FULL_NAME} email"
    body = (
        f"Hello {display_name},\n\n"
        "Confirm that this email belongs to your Pulsar-Frequency House account.\n\n"
        f"Verify within {VERIFY_TTL_HOURS} hours:\n{verify_url}\n\n"
        "If you did not create this account, you can ignore this message.\n\n"
        f"{ENDORSEMENT}\n"
    )
    return send_email(email, subject, body)


def _deliver_requested_verification(email: str, display_name: str, token: str) -> None:
    try:
        deliver_email_verification(email, display_name, token)
    except Exception:
        pass


@router.post("/auth/email-verification/request")
def request_email_verification(
    payload: EmailVerificationRequest,
    background_tasks: BackgroundTasks,
):
    issued = service.request_for_email(payload.email)
    if issued.get("issued"):
        background_tasks.add_task(
            _deliver_requested_verification,
            str(issued["email"]),
            str(issued["display_name"]),
            str(issued["token"]),
        )
    return {
        "accepted": True,
        "message": "If that account needs verification, a verification email will be sent.",
    }


@router.post("/auth/email-verification/confirm")
def confirm_email_verification(payload: EmailVerificationConfirm):
    try:
        result = service.complete(payload.token)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "verified": result["verified"],
        "message": "Email verified. Your membership request can now be approved.",
    }


def _verification_page() -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Verify email — {escape(PRODUCT_FULL_NAME)}</title><style>
body{{font-family:system-ui,sans-serif;background:#0d0715;color:#fff;margin:0;padding:28px}}
.card{{max-width:620px;margin:60px auto;padding:28px;border-radius:24px;background:#1d1230;box-shadow:0 20px 60px #0008}}
.muted{{color:#c9bfd5}} button{{padding:13px;border:0;border-radius:11px;background:#f2c35c;color:#1a1024;font-weight:800;cursor:pointer}}
a{{color:#f2c35c}}
</style></head><body><div class='card'><h1>{escape(PRODUCT_FULL_NAME)}</h1><p class='muted'>{escape(TAGLINE)}</p>
<h2>Verify your email</h2><p id='status' class='muted'>Checking your verification link…</p><p><a href='/signin'>Continue to sign in</a></p></div>
<script>
const status=document.getElementById('status');
const token=new URLSearchParams(window.location.hash.slice(1)).get('token') || '';
try{{history.replaceState({{}},'', '/auth/verify-email')}}catch(e){{}}
(async()=>{{
 if(!token){{status.textContent='This verification link is missing its secure token.';return;}}
 try{{
  const r=await fetch('/auth/email-verification/confirm',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token}})}});
  const data=await r.json();
  status.textContent=r.ok ? (data.message || 'Email verified.') : (data.detail || 'This verification link is invalid or expired.');
 }}catch(e){{status.textContent='Verification could not be completed. Please try again.';}}
}})();
</script></body></html>"""
    )


@router.get("/auth/verify-email", response_class=HTMLResponse)
def verify_email_page():
    return _verification_page()


__all__ = [
    "EmailVerificationService",
    "deliver_email_verification",
    "router",
    "service",
]
