from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .plans import get_plan

# PBKDF2 remains only for transparent verification/migration of existing accounts.
PBKDF2_ITERATIONS = 310_000
PASSWORD_SCHEME_ARGON2ID = "argon2id"
PASSWORD_SCHEME_PBKDF2 = "pbkdf2_sha256"
SESSION_DAYS = 30
APPROVAL_HOURS = 72
LOGIN_WINDOW_MINUTES = 15
LOGIN_FAILURE_THRESHOLD = 5
LOGIN_BLOCK_BASE_SECONDS = 30
LOGIN_BLOCK_MAX_SECONDS = 15 * 60

# OWASP-aligned Argon2id baseline: 19 MiB memory, 2 iterations, one lane.
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)
_DUMMY_ARGON2_HASH = _PASSWORD_HASHER.hash("pulsar-frequency-house-dummy-password")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@") or len(value) > 254:
        raise ValueError("Enter a valid email address")
    return value


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _validate_password(password: str) -> str:
    value = password or ""
    if len(value) < 10:
        raise ValueError("Password must be at least 10 characters")
    if len(value) > 512:
        raise ValueError("Password is too long")
    return value


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Legacy PBKDF2 helper retained for existing-account migration/tests only."""
    value = _validate_password(password)
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        _, candidate = _hash_password(password, salt)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, digest_hex)


def _hash_password_argon2id(password: str) -> str:
    return _PASSWORD_HASHER.hash(_validate_password(password))


def _verify_password_argon2id(password: str, digest: str) -> bool:
    try:
        return bool(_PASSWORD_HASHER.verify(digest, password or ""))
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
        return False


def _dummy_password_verify(password: str) -> None:
    """Reduce known-vs-unknown-account timing differences without storing a fake user."""
    try:
        _PASSWORD_HASHER.verify(_DUMMY_ARGON2_HASH, password or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
        pass


@dataclass
class SignupResult:
    user_id: str
    membership_request_id: str
    approval_token: str
    email: str
    display_name: str
    requested_plan: str


class AccountStore:
    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_scheme TEXT NOT NULL DEFAULT 'argon2id',
                    password_updated_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    plan_id TEXT NOT NULL DEFAULT 'free',
                    requested_plan_id TEXT NOT NULL DEFAULT 'free',
                    billing_status TEXT NOT NULL DEFAULT 'not_required',
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by TEXT,
                    rejected_at TEXT,
                    rejected_by TEXT,
                    disabled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS membership_requests (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    requested_plan_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    project_id TEXT,
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS song_slots (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'draft',
                    generation_count INTEGER NOT NULL DEFAULT 0,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, project_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auth_login_throttle (
                    identity_hash TEXT PRIMARY KEY,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL,
                    last_failed_at TEXT NOT NULL,
                    blocked_until TEXT
                );
                """
            )

            # In-place migration for databases created before the Argon2id rollout.
            columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
            if "password_scheme" not in columns:
                con.execute(
                    "ALTER TABLE users ADD COLUMN password_scheme TEXT NOT NULL DEFAULT 'pbkdf2_sha256'"
                )
            if "password_updated_at" not in columns:
                con.execute("ALTER TABLE users ADD COLUMN password_updated_at TEXT")

    @staticmethod
    def _login_identity_hash(email: str) -> str:
        try:
            normalized = _normalize_email(email)
        except ValueError:
            normalized = (email or "").strip().lower()[:254]
        return _hash_secret("login:" + normalized)

    def login_throttle_status(self, email: str) -> dict:
        identity = self._login_identity_hash(email)
        now = _utcnow()
        with self._connect() as con:
            row = con.execute(
                "SELECT failure_count,window_started_at,last_failed_at,blocked_until FROM auth_login_throttle WHERE identity_hash=?",
                (identity,),
            ).fetchone()
        if not row:
            return {"blocked": False, "failure_count": 0, "retry_after_seconds": 0}
        blocked_until = _parse_iso(row["blocked_until"])
        retry = max(0, int((blocked_until - now).total_seconds())) if blocked_until else 0
        return {
            "blocked": bool(blocked_until and blocked_until > now),
            "failure_count": int(row["failure_count"]),
            "retry_after_seconds": retry,
        }

    def _record_login_failure(self, email: str) -> None:
        identity = self._login_identity_hash(email)
        now = _utcnow()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT failure_count,window_started_at,last_failed_at FROM auth_login_throttle WHERE identity_hash=?",
                (identity,),
            ).fetchone()
            window_start = _parse_iso(row["window_started_at"]) if row else None
            if not window_start or now - window_start > timedelta(minutes=LOGIN_WINDOW_MINUTES):
                failures = 1
                window_start = now
            else:
                failures = int(row["failure_count"]) + 1

            blocked_until = None
            if failures >= LOGIN_FAILURE_THRESHOLD:
                exponent = min(failures - LOGIN_FAILURE_THRESHOLD, 5)
                seconds = min(LOGIN_BLOCK_MAX_SECONDS, LOGIN_BLOCK_BASE_SECONDS * (2**exponent))
                blocked_until = _iso(now + timedelta(seconds=seconds))

            con.execute(
                """INSERT INTO auth_login_throttle
                   (identity_hash,failure_count,window_started_at,last_failed_at,blocked_until)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(identity_hash) DO UPDATE SET
                     failure_count=excluded.failure_count,
                     window_started_at=excluded.window_started_at,
                     last_failed_at=excluded.last_failed_at,
                     blocked_until=excluded.blocked_until""",
                (identity, failures, _iso(window_start), _iso(now), blocked_until),
            )

    def clear_login_throttle(self, email: str) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM auth_login_throttle WHERE identity_hash=?",
                (self._login_identity_hash(email),),
            )

    def signup(self, email: str, display_name: str, password: str, requested_plan: str) -> SignupResult:
        email = _normalize_email(email)
        display_name = (display_name or "").strip()
        if len(display_name) < 2:
            raise ValueError("Display name must contain at least 2 characters")
        plan = get_plan(requested_plan)
        digest = _hash_password_argon2id(password)
        user_id = uuid4().hex
        request_id = uuid4().hex
        token = secrets.token_urlsafe(32)
        token_hash = _hash_secret(token)
        created = _utcnow()
        expires = created + timedelta(hours=APPROVAL_HOURS)
        billing_status = "not_required" if plan.id == "free" else "awaiting_approval"

        with self._connect() as con:
            existing = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                raise ValueError("An account already exists for this email address")
            con.execute(
                """INSERT INTO users
                   (id,email,display_name,password_salt,password_hash,password_scheme,password_updated_at,
                    status,plan_id,requested_plan_id,billing_status,created_at)
                   VALUES (?,?,?,?,?,?,?,'pending_approval','free',?,?,?)""",
                (
                    user_id,
                    email,
                    display_name,
                    "",
                    digest,
                    PASSWORD_SCHEME_ARGON2ID,
                    _iso(created),
                    plan.id,
                    billing_status,
                    _iso(created),
                ),
            )
            con.execute(
                """INSERT INTO membership_requests
                   (id,user_id,requested_plan_id,token_hash,status,created_at,expires_at)
                   VALUES (?,?,?,?, 'pending', ?, ?)""",
                (request_id, user_id, plan.id, token_hash, _iso(created), _iso(expires)),
            )
        return SignupResult(user_id, request_id, token, email, display_name, plan.id)

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        try:
            email = _normalize_email(email)
        except ValueError:
            return None
        with self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None

    def authenticate(self, email: str, password: str) -> dict | None:
        # All login surfaces share this persistent account-level throttle, so switching
        # between HTML and JSON login endpoints cannot bypass the attempt limit.
        if self.login_throttle_status(email)["blocked"]:
            return None

        user = self.get_user_by_email(email)
        if not user:
            _dummy_password_verify(password)
            self._record_login_failure(email)
            return None

        scheme = str(user.get("password_scheme") or PASSWORD_SCHEME_PBKDF2).lower()
        valid = False
        needs_upgrade = False
        if scheme == PASSWORD_SCHEME_ARGON2ID:
            valid = _verify_password_argon2id(password, user["password_hash"])
            if valid:
                try:
                    needs_upgrade = _PASSWORD_HASHER.check_needs_rehash(user["password_hash"])
                except (InvalidHashError, TypeError):
                    needs_upgrade = True
        elif scheme == PASSWORD_SCHEME_PBKDF2:
            valid = _verify_password(password, user["password_salt"], user["password_hash"])
            needs_upgrade = valid
        else:
            _dummy_password_verify(password)

        if not valid:
            self._record_login_failure(email)
            return None

        # A disabled account must fail closed at the canonical credential boundary.
        # Keep the same generic authentication result used for bad credentials.
        if user.get("disabled_at"):
            self._record_login_failure(email)
            return None

        self.clear_login_throttle(email)
        if needs_upgrade:
            upgraded = _hash_password_argon2id(password)
            with self._connect() as con:
                con.execute(
                    """UPDATE users SET password_salt='',password_hash=?,password_scheme=?,password_updated_at=?
                       WHERE id=?""",
                    (upgraded, PASSWORD_SCHEME_ARGON2ID, _iso(), user["id"]),
                )
            return self.get_user(user["id"]) or user
        return user

    def create_session(self, user_id: str) -> str:
        user = self.get_user(user_id)
        if not user or user.get("disabled_at"):
            raise PermissionError("Eligible account required")
        token = secrets.token_urlsafe(32)
        now = _utcnow()
        with self._connect() as con:
            con.execute(
                "INSERT INTO sessions (id,user_id,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)",
                (uuid4().hex, user_id, _hash_secret(token), _iso(now), _iso(now + timedelta(days=SESSION_DAYS))),
            )
        return token

    def resolve_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._connect() as con:
            row = con.execute(
                """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?
                     AND u.disabled_at IS NULL""",
                (_hash_secret(token), _iso()),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as con:
            con.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=?", (_iso(), _hash_secret(token)))

    def membership_request_from_token(self, token: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT mr.*, u.email, u.display_name, u.status AS user_status, u.billing_status
                   FROM membership_requests mr JOIN users u ON u.id=mr.user_id
                   WHERE mr.token_hash=?""",
                (_hash_secret(token),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["expired"] = item["expires_at"] <= _iso()
        return item

    def decide_membership(self, token: str, decision: str, decided_by: str) -> dict:
        decision = decision.strip().lower()
        if decision not in {"approve", "reject"}:
            raise ValueError("Decision must be approve or reject")
        decided_by = (decided_by or "ESP Owner").strip()[:120]
        token_hash = _hash_secret(token)
        now = _iso()

        with self._connect() as con:
            request = con.execute(
                "SELECT * FROM membership_requests WHERE token_hash=?", (token_hash,)
            ).fetchone()
            if not request:
                raise ValueError("Membership request not found")
            if request["status"] != "pending":
                raise ValueError("Membership request has already been decided")
            if request["expires_at"] <= now:
                con.execute("UPDATE membership_requests SET status='expired', decided_at=? WHERE id=?", (now, request["id"]))
                raise ValueError("Membership approval link has expired")

            user = con.execute("SELECT * FROM users WHERE id=?", (request["user_id"],)).fetchone()
            if not user:
                raise ValueError("Applicant account no longer exists")

            if decision == "approve":
                requested_plan = get_plan(request["requested_plan_id"])
                billing_status = "not_required" if requested_plan.id == "free" else "awaiting_payment"
                status = "active" if requested_plan.id == "free" else "approved_pending_payment"
                con.execute(
                    """UPDATE users SET status=?, requested_plan_id=?, billing_status=?, approved_at=?, approved_by=?
                       WHERE id=?""",
                    (status, requested_plan.id, billing_status, now, decided_by, user["id"]),
                )
                con.execute(
                    "UPDATE membership_requests SET status='approved', decided_at=?, decided_by=? WHERE id=?",
                    (now, decided_by, request["id"]),
                )
            else:
                con.execute(
                    "UPDATE users SET status='rejected', rejected_at=?, rejected_by=? WHERE id=?",
                    (now, decided_by, user["id"]),
                )
                con.execute(
                    "UPDATE membership_requests SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
                    (now, decided_by, request["id"]),
                )
        return self.get_user(request["user_id"]) or {}

    def activate_paid_plan(self, user_id: str, plan_id: str, billing_reference: str | None = None) -> dict:
        plan = get_plan(plan_id)
        if plan.id == "free":
            raise ValueError("Free plan does not require paid activation")
        with self._connect() as con:
            user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            if user["status"] not in {"approved_pending_payment", "active"}:
                raise ValueError("Membership must be approved before a paid plan can activate")
            con.execute(
                "UPDATE users SET status='active', plan_id=?, requested_plan_id=?, billing_status='active' WHERE id=?",
                (plan.id, plan.id, user_id),
            )
        return self.get_user(user_id) or {}

    def pending_requests(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT mr.id, mr.requested_plan_id, mr.created_at, mr.expires_at,
                          u.id AS user_id, u.email, u.display_name
                   FROM membership_requests mr JOIN users u ON u.id=mr.user_id
                   WHERE mr.status='pending' ORDER BY mr.created_at ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def record_usage(self, user_id: str, event_type: str, project_id: str | None = None, metadata_json: str | None = None) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO usage_events (id,user_id,event_type,project_id,occurred_at,metadata_json) VALUES (?,?,?,?,?,?)",
                (uuid4().hex, user_id, event_type, project_id, _iso(), metadata_json),
            )

    def start_song_slot(self, user_id: str, project_id: str, local_date: str) -> dict:
        user = self.get_user(user_id)
        if not user or user["status"] != "active":
            raise PermissionError("Active membership required")
        plan = get_plan(user["plan_id"])
        with self._connect() as con:
            row = con.execute("SELECT * FROM song_slots WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
            if not row:
                if plan.confirmed_songs_per_day == 0:
                    raise PermissionError("This plan does not include confirmed full-song slots")
                if plan.confirmed_songs_per_day is not None:
                    confirmed = con.execute(
                        "SELECT COUNT(*) AS n FROM song_slots WHERE user_id=? AND local_date=? AND state='confirmed'",
                        (user_id, local_date),
                    ).fetchone()["n"]
                    if confirmed >= plan.confirmed_songs_per_day:
                        raise PermissionError("Daily confirmed-song allowance has been reached")
                slot_id = uuid4().hex
                con.execute(
                    "INSERT INTO song_slots (id,user_id,project_id,local_date,state,generation_count,created_at) VALUES (?,?,?,?, 'draft',0,?)",
                    (slot_id, user_id, project_id, local_date, _iso()),
                )
                row = con.execute("SELECT * FROM song_slots WHERE id=?", (slot_id,)).fetchone()
            return dict(row)

    def record_regeneration(self, user_id: str, project_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM song_slots WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
            if not row:
                raise ValueError("Song slot not started")
            if row["state"] == "confirmed":
                raise PermissionError("Confirmed songs cannot use the pre-confirmation regeneration allowance")
            con.execute("UPDATE song_slots SET generation_count=generation_count+1 WHERE id=?", (row["id"],))
            row = con.execute("SELECT * FROM song_slots WHERE id=?", (row["id"],)).fetchone()
        return dict(row)

    def confirm_song(self, user_id: str, project_id: str) -> dict:
        user = self.get_user(user_id)
        if not user or user["status"] != "active":
            raise PermissionError("Active membership required")
        plan = get_plan(user["plan_id"])
        with self._connect() as con:
            row = con.execute("SELECT * FROM song_slots WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
            if not row:
                raise ValueError("Song slot not started")
            if row["state"] == "confirmed":
                return dict(row)
            if plan.confirmed_songs_per_day is not None:
                confirmed = con.execute(
                    "SELECT COUNT(*) AS n FROM song_slots WHERE user_id=? AND local_date=? AND state='confirmed'",
                    (user_id, row["local_date"]),
                ).fetchone()["n"]
                if confirmed >= plan.confirmed_songs_per_day:
                    raise PermissionError("Daily confirmed-song allowance has been reached")
            con.execute("UPDATE song_slots SET state='confirmed', confirmed_at=? WHERE id=?", (_iso(), row["id"]))
            row = con.execute("SELECT * FROM song_slots WHERE id=?", (row["id"],)).fetchone()
        return dict(row)