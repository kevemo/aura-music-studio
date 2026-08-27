from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_store import AuraSecStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuraSecEnrollmentStore:
    """One-time device-enrolment challenge state.

    The native protocol/attestation layer must verify proof of possession or platform
    attestation before calling `complete_verified_enrollment`. A browser form alone can
    never create a protected-device identity.
    """

    def __init__(self, accounts: AccountStore | None = None, security: AuraSecStore | None = None):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.accounts.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_sec_enrollment_challenges (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    challenge_hash TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    device_id TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_enrollment_user_status
                ON aura_sec_enrollment_challenges(user_id, status, expires_at);
                """
            )

    def create_challenge(
        self,
        user_id: str,
        *,
        display_name: str,
        platform: str,
        architecture: str,
        ttl_minutes: int = 10,
    ) -> dict:
        licence = self.security.licence(user_id)
        if licence.get("status") != "active":
            raise PermissionError("Active Aura Sec licence required for device enrolment")
        active_devices = [item for item in self.security.list_devices(user_id) if item.get("status") != "revoked"]
        if len(active_devices) >= int(licence.get("device_limit") or 0):
            raise PermissionError("Aura Sec device limit reached")
        if not 1 <= int(ttl_minutes) <= 30:
            raise ValueError("Enrollment challenge lifetime must be between 1 and 30 minutes")

        name = (display_name or "").strip()[:120]
        platform_value = (platform or "").strip().lower()[:40]
        architecture_value = (architecture or "").strip().lower()[:40]
        if not name or len(platform_value) < 2 or len(architecture_value) < 2:
            raise ValueError("Device name, platform and architecture are required")

        challenge_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(minutes=int(ttl_minutes))
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_enrollment_challenges
                   (id,user_id,challenge_hash,display_name,platform,architecture,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,'pending',?,?)""",
                (
                    challenge_id,
                    user_id,
                    _hash_secret(secret),
                    name,
                    platform_value,
                    architecture_value,
                    _iso(now),
                    _iso(expires),
                ),
            )
        return {
            "challenge_id": challenge_id,
            "challenge": secret,
            "expires_at": _iso(expires),
            "display_name": name,
            "platform": platform_value,
            "architecture": architecture_value,
            "one_time": True,
        }

    def _challenge(self, user_id: str, challenge_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_enrollment_challenges WHERE user_id=? AND id=?",
                (user_id, challenge_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec enrollment challenge not found")
        return dict(row)

    def complete_verified_enrollment(
        self,
        user_id: str,
        challenge_id: str,
        *,
        challenge: str,
        proof_verified: bool,
        public_key_fingerprint: str,
        now: datetime | None = None,
    ) -> dict:
        """Consume a challenge only after an external verifier validates device proof.

        `proof_verified` must be produced by the native attestation/proof layer; this store
        does not attempt to implement platform attestation cryptography itself.
        """
        if not proof_verified:
            raise PermissionError("Aura Sec device proof/attestation was not verified")
        item = self._challenge(user_id, challenge_id)
        if item.get("status") != "pending" or item.get("consumed_at"):
            raise PermissionError("Aura Sec enrollment challenge has already been used")

        current = (now or _now()).astimezone(timezone.utc)
        expires = datetime.fromisoformat(item["expires_at"]).astimezone(timezone.utc)
        if current >= expires:
            with self._connect() as con:
                con.execute(
                    "UPDATE aura_sec_enrollment_challenges SET status='expired' WHERE user_id=? AND id=?",
                    (user_id, challenge_id),
                )
            raise PermissionError("Aura Sec enrollment challenge has expired")

        supplied = _hash_secret((challenge or "").strip())
        if not secrets.compare_digest(supplied, item["challenge_hash"]):
            raise PermissionError("Aura Sec enrollment challenge proof does not match")

        licence = self.security.licence(user_id)
        if licence.get("status") != "active":
            raise PermissionError("Active Aura Sec licence required at enrolment completion")

        device = self.security.enroll_attested_device(
            user_id,
            display_name=item["display_name"],
            platform=item["platform"],
            architecture=item["architecture"],
            public_key_fingerprint=public_key_fingerprint,
        )
        with self._connect() as con:
            updated = con.execute(
                """UPDATE aura_sec_enrollment_challenges
                   SET status='consumed',consumed_at=?,device_id=?
                   WHERE user_id=? AND id=? AND status='pending' AND consumed_at IS NULL""",
                (_iso(current), device["id"], user_id, challenge_id),
            )
            if updated.rowcount != 1:
                # Extremely defensive race handling: revoke the just-created device if the
                # one-time challenge lost a consumption race.
                self.security.revoke_device(user_id, device["id"])
                raise PermissionError("Aura Sec enrollment challenge was consumed concurrently")
        return {
            "enrolled": True,
            "device": device,
            "challenge_id": challenge_id,
            "protection_state": "awaiting_heartbeat",
            "message": "Device identity enrolled. Protection is not healthy until a signed heartbeat is verified.",
        }


__all__ = ["AuraSecEnrollmentStore"]
