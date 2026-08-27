from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

OWNER_MFA_CHALLENGE_COOKIE = "lss_owner_mfa_challenge"
OWNER_MFA_CHALLENGE_MINUTES = 5
OWNER_MFA_MAX_ATTEMPTS = 5
OWNER_TOTP_STEP_SECONDS = 30
OWNER_TOTP_DIGITS = 6
OWNER_TOTP_WINDOW = 1
_OWNER_PERSONAS = {"mary", "kev"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def owner_mfa_required() -> bool:
    return _truthy(os.getenv("LSS_OWNER_MFA_REQUIRED"))


def _secret_name(persona: str) -> str:
    if persona not in _OWNER_PERSONAS:
        raise ValueError("Unknown owner persona")
    return f"LSS_OWNER_{persona.upper()}_TOTP_SECRET"


def _configured_secret(persona: str) -> str:
    return (os.getenv(_secret_name(persona)) or "").strip().replace(" ", "").upper()


def _decode_secret(secret: str) -> bytes:
    clean = (secret or "").strip().replace(" ", "").upper()
    if not clean:
        raise ValueError("Owner MFA secret is not configured")
    padding = "=" * ((8 - len(clean) % 8) % 8)
    try:
        decoded = base64.b32decode(clean + padding, casefold=True)
    except Exception as exc:
        raise ValueError("Owner MFA secret is not valid Base32") from exc
    if len(decoded) < 20:
        raise ValueError("Owner MFA secret must contain at least 160 bits")
    return decoded


def owner_mfa_configured() -> bool:
    try:
        _decode_secret(_configured_secret("mary"))
        _decode_secret(_configured_secret("kev"))
    except ValueError:
        return False
    return True


def _totp_code(secret: str, counter: int) -> str:
    key = _decode_secret(secret)
    message = int(counter).to_bytes(8, "big", signed=False)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    value = truncated % (10**OWNER_TOTP_DIGITS)
    return f"{value:0{OWNER_TOTP_DIGITS}d}"


def _current_counter(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // OWNER_TOTP_STEP_SECONDS)


@dataclass(frozen=True)
class OwnerMFAChallenge:
    persona: str
    purpose: str
    expires_at: str
    attempts: int


class OwnerMFAService:
    """Second-factor challenge and TOTP replay protection for Mary/Kev owner access.

    TOTP seeds are read only from deployment environment variables. They are never written
    to SQLite, returned to the browser, or included in audit payloads. Challenge tokens are
    random browser secrets; SQLite stores only SHA-256 challenge hashes.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_mfa_challenges (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    persona TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_owner_mfa_challenge_hash
                    ON owner_mfa_challenges(token_hash);

                CREATE TABLE IF NOT EXISTS owner_mfa_totp_replay (
                    persona TEXT PRIMARY KEY,
                    last_counter INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def configuration_status() -> dict:
        return {
            "required": owner_mfa_required(),
            "configured": owner_mfa_configured(),
            "personas": ["mary", "kev"],
            "totp_step_seconds": OWNER_TOTP_STEP_SECONDS,
            "challenge_minutes": OWNER_MFA_CHALLENGE_MINUTES,
            "max_attempts": OWNER_MFA_MAX_ATTEMPTS,
        }

    def create_challenge(self, persona: str, *, purpose: str) -> str:
        persona = (persona or "").strip().lower()
        purpose = (purpose or "").strip().lower()
        if persona not in _OWNER_PERSONAS:
            raise ValueError("Choose Mary or Kev")
        if purpose not in {"login", "switch"}:
            raise ValueError("Unsupported owner MFA purpose")
        if owner_mfa_required() and not owner_mfa_configured():
            raise RuntimeError("Owner MFA is required but not fully configured")

        token = secrets.token_urlsafe(48)
        now = _now()
        expires = now + timedelta(minutes=OWNER_MFA_CHALLENGE_MINUTES)
        with self._connect() as con:
            con.execute(
                """INSERT INTO owner_mfa_challenges
                   (id,token_hash,persona,purpose,created_at,expires_at,attempts,consumed_at)
                   VALUES (?,?,?,?,?,?,0,NULL)""",
                (uuid4().hex, _hash(token), persona, purpose, _iso(now), _iso(expires)),
            )
            con.execute(
                "DELETE FROM owner_mfa_challenges WHERE expires_at<? OR (consumed_at IS NOT NULL AND consumed_at<?)",
                (_iso(now - timedelta(days=1)), _iso(now - timedelta(days=7))),
            )
        return token

    def challenge(self, token: str | None) -> OwnerMFAChallenge | None:
        if not token:
            return None
        with self._connect() as con:
            row = con.execute(
                """SELECT persona,purpose,expires_at,attempts,consumed_at
                   FROM owner_mfa_challenges WHERE token_hash=?""",
                (_hash(token),),
            ).fetchone()
        if not row or row["consumed_at"]:
            return None
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return None
        if expires <= _now() or int(row["attempts"]) >= OWNER_MFA_MAX_ATTEMPTS:
            return None
        return OwnerMFAChallenge(
            persona=str(row["persona"]),
            purpose=str(row["purpose"]),
            expires_at=str(row["expires_at"]),
            attempts=int(row["attempts"]),
        )

    def _matching_counter(self, persona: str, code: str, *, now: float | None = None) -> int | None:
        if persona not in _OWNER_PERSONAS or len(code) != OWNER_TOTP_DIGITS or not code.isdigit():
            return None
        secret = _configured_secret(persona)
        try:
            center = _current_counter(now)
            for delta in range(-OWNER_TOTP_WINDOW, OWNER_TOTP_WINDOW + 1):
                counter = center + delta
                if counter < 0:
                    continue
                if secrets.compare_digest(_totp_code(secret, counter), code):
                    return counter
        except ValueError:
            return None
        return None

    def verify_challenge(
        self,
        token: str | None,
        code: str,
        *,
        expected_purpose: str,
        expected_persona: str | None = None,
        now: float | None = None,
    ) -> str:
        if owner_mfa_required() and not owner_mfa_configured():
            raise RuntimeError("Owner MFA is required but not fully configured")
        if not token:
            raise ValueError("Owner MFA challenge is missing or expired")
        token_hash = _hash(token)
        timestamp = _now() if now is None else datetime.fromtimestamp(now, tz=timezone.utc)
        error: str | None = None
        verified_persona: str | None = None

        # Do not raise application-level verification errors inside this context manager.
        # sqlite3.Connection.__exit__ rolls back when an exception escapes; failed-attempt,
        # expiry and replay state must commit before the caller receives the failure.
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM owner_mfa_challenges WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row or row["consumed_at"]:
                error = "Owner MFA challenge is missing or expired"
            else:
                try:
                    expires = datetime.fromisoformat(row["expires_at"])
                except (TypeError, ValueError):
                    expires = None
                    error = "Owner MFA challenge is missing or expired"

                if error is None and (expires is None or expires <= timestamp):
                    con.execute(
                        "UPDATE owner_mfa_challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                        (_iso(timestamp), row["id"]),
                    )
                    error = "Owner MFA challenge is missing or expired"
                elif error is None and int(row["attempts"]) >= OWNER_MFA_MAX_ATTEMPTS:
                    error = "Owner MFA challenge is exhausted"

                if error is None:
                    persona = str(row["persona"])
                    purpose = str(row["purpose"])
                    if purpose != expected_purpose or (expected_persona and persona != expected_persona):
                        error = "Owner MFA challenge does not match this action"
                    else:
                        accepted_counter = self._matching_counter(persona, (code or "").strip(), now=now)
                        if accepted_counter is None:
                            attempts = int(row["attempts"]) + 1
                            consumed = _iso(timestamp) if attempts >= OWNER_MFA_MAX_ATTEMPTS else None
                            con.execute(
                                "UPDATE owner_mfa_challenges SET attempts=?,consumed_at=? WHERE id=?",
                                (attempts, consumed, row["id"]),
                            )
                            error = "Incorrect owner verification code"
                        else:
                            replay = con.execute(
                                "SELECT last_counter FROM owner_mfa_totp_replay WHERE persona=?",
                                (persona,),
                            ).fetchone()
                            if replay and accepted_counter <= int(replay["last_counter"]):
                                attempts = int(row["attempts"]) + 1
                                consumed = _iso(timestamp) if attempts >= OWNER_MFA_MAX_ATTEMPTS else None
                                con.execute(
                                    "UPDATE owner_mfa_challenges SET attempts=?,consumed_at=? WHERE id=?",
                                    (attempts, consumed, row["id"]),
                                )
                                error = "Owner verification code has already been used"
                            else:
                                con.execute(
                                    """INSERT INTO owner_mfa_totp_replay(persona,last_counter,updated_at)
                                       VALUES (?,?,?)
                                       ON CONFLICT(persona) DO UPDATE SET
                                         last_counter=excluded.last_counter,
                                         updated_at=excluded.updated_at""",
                                    (persona, accepted_counter, _iso(timestamp)),
                                )
                                con.execute(
                                    "UPDATE owner_mfa_challenges SET consumed_at=? WHERE id=?",
                                    (_iso(timestamp), row["id"]),
                                )
                                verified_persona = persona

        if error is not None:
            raise ValueError(error)
        if verified_persona is None:
            raise ValueError("Owner MFA challenge is missing or expired")
        return verified_persona


_service: OwnerMFAService | None = None


def service() -> OwnerMFAService:
    global _service
    desired = Path(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3").resolve()
    if _service is None or _service.db_path.resolve() != desired:
        _service = OwnerMFAService(desired)
    return _service


__all__ = [
    "OWNER_MFA_CHALLENGE_COOKIE",
    "OWNER_MFA_CHALLENGE_MINUTES",
    "OWNER_MFA_MAX_ATTEMPTS",
    "OwnerMFAChallenge",
    "OwnerMFAService",
    "owner_mfa_configured",
    "owner_mfa_required",
    "service",
]
