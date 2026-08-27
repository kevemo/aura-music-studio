from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_store import AuraSecStore


_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_PROOF_TYPES = {"device_key_possession", "platform_attestation", "hardware_attestation"}
_ALLOWED_KEY_ALGORITHMS = {"ed25519", "p256", "rsa-pss-sha256"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EnrollmentVerificationContext:
    user_id: str
    challenge_id: str
    challenge: str
    display_name: str
    platform: str
    architecture: str
    expires_at: str


@dataclass(frozen=True)
class VerifiedEnrollmentProof:
    """Result returned only by a trusted native proof/attestation verifier.

    The private device key, platform attestation secret and raw biometric information are
    never stored here. `evidence_digest` is a SHA-256 audit digest of the verified evidence
    retained by the verifier or attestation service.
    """

    public_key_fingerprint: str
    proof_type: str
    verifier_id: str
    evidence_digest: str
    platform: str
    architecture: str
    key_algorithm: str
    hardware_backed: bool = False


EnrollmentProofVerifier = Callable[[dict, EnrollmentVerificationContext], VerifiedEnrollmentProof | None]


class AuraSecEnrollmentStore:
    """One-time native device-enrolment challenge state.

    A browser form cannot create a protected-device identity. Completion requires a
    trusted verifier callback that validates device-key possession and/or platform
    attestation against the exact one-time server challenge and returns structured proof.
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
                    verifier_id TEXT,
                    proof_type TEXT,
                    evidence_digest TEXT,
                    key_algorithm TEXT,
                    hardware_backed INTEGER,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_enrollment_user_status
                ON aura_sec_enrollment_challenges(user_id, status, expires_at);
                """
            )
            existing = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(aura_sec_enrollment_challenges)").fetchall()
            }
            migrations = {
                "verifier_id": "TEXT",
                "proof_type": "TEXT",
                "evidence_digest": "TEXT",
                "key_algorithm": "TEXT",
                "hardware_backed": "INTEGER",
            }
            for column, sql_type in migrations.items():
                if column not in existing:
                    con.execute(
                        f"ALTER TABLE aura_sec_enrollment_challenges ADD COLUMN {column} {sql_type}"
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
            "browser_can_self_verify": False,
        }

    def _challenge(self, user_id: str, challenge_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_enrollment_challenges WHERE user_id=? AND id=?",
                (user_id, challenge_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec enrollment challenge not found")
        item = dict(row)
        if item.get("hardware_backed") is not None:
            item["hardware_backed"] = bool(item["hardware_backed"])
        return item

    def _pending_context(
        self,
        user_id: str,
        challenge_id: str,
        *,
        challenge: str,
        now: datetime | None = None,
    ) -> tuple[dict, EnrollmentVerificationContext, datetime]:
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

        supplied_challenge = (challenge or "").strip()
        supplied = _hash_secret(supplied_challenge)
        if not secrets.compare_digest(supplied, item["challenge_hash"]):
            raise PermissionError("Aura Sec enrollment challenge proof does not match")

        licence = self.security.licence(user_id)
        if licence.get("status") != "active":
            raise PermissionError("Active Aura Sec licence required at enrolment completion")

        context = EnrollmentVerificationContext(
            user_id=user_id,
            challenge_id=challenge_id,
            challenge=supplied_challenge,
            display_name=item["display_name"],
            platform=item["platform"],
            architecture=item["architecture"],
            expires_at=item["expires_at"],
        )
        return item, context, current

    @staticmethod
    def _validate_verified_proof(
        proof: VerifiedEnrollmentProof,
        context: EnrollmentVerificationContext,
    ) -> VerifiedEnrollmentProof:
        if not isinstance(proof, VerifiedEnrollmentProof):
            raise PermissionError("Trusted Aura Sec enrollment verifier did not return verified proof")

        fingerprint = (proof.public_key_fingerprint or "").strip().lower()
        evidence_digest = (proof.evidence_digest or "").strip().lower()
        verifier_id = (proof.verifier_id or "").strip()
        proof_type = (proof.proof_type or "").strip().lower()
        platform = (proof.platform or "").strip().lower()
        architecture = (proof.architecture or "").strip().lower()
        key_algorithm = (proof.key_algorithm or "").strip().lower()

        if not _HEX_256.fullmatch(fingerprint):
            raise PermissionError("Verified device public-key fingerprint must be SHA-256")
        if not _HEX_256.fullmatch(evidence_digest):
            raise PermissionError("Verified enrollment evidence digest must be SHA-256")
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError("Trusted enrollment verifier identity is required")
        if proof_type not in _ALLOWED_PROOF_TYPES:
            raise PermissionError("Unsupported Aura Sec enrollment proof type")
        if key_algorithm not in _ALLOWED_KEY_ALGORITHMS:
            raise PermissionError("Unsupported Aura Sec device-key algorithm")
        if platform != context.platform or architecture != context.architecture:
            raise PermissionError("Verified device proof does not match the requested platform/architecture")

        return VerifiedEnrollmentProof(
            public_key_fingerprint=fingerprint,
            proof_type=proof_type,
            verifier_id=verifier_id,
            evidence_digest=evidence_digest,
            platform=platform,
            architecture=architecture,
            key_algorithm=key_algorithm,
            hardware_backed=bool(proof.hardware_backed),
        )

    def verify_and_complete_enrollment(
        self,
        user_id: str,
        challenge_id: str,
        *,
        challenge: str,
        proof_payload: dict,
        verifier: EnrollmentProofVerifier | None,
        now: datetime | None = None,
    ) -> dict:
        """Verify native proof against the exact challenge, then enrol the device.

        The caller supplies a trusted verifier adapter for the target platform. This
        method deliberately has no `proof_verified=True` escape hatch: a boolean cannot
        cross the device trust boundary.
        """
        _item, context, current = self._pending_context(
            user_id,
            challenge_id,
            challenge=challenge,
            now=now,
        )
        if verifier is None:
            raise PermissionError("A trusted Aura Sec native enrollment verifier is required")

        verified = verifier(dict(proof_payload or {}), context)
        if verified is None:
            raise PermissionError("Aura Sec device proof/attestation was not verified")
        proof = self._validate_verified_proof(verified, context)

        device = self.security.enroll_attested_device(
            user_id,
            display_name=context.display_name,
            platform=context.platform,
            architecture=context.architecture,
            public_key_fingerprint=proof.public_key_fingerprint,
        )
        with self._connect() as con:
            updated = con.execute(
                """UPDATE aura_sec_enrollment_challenges
                   SET status='consumed',consumed_at=?,device_id=?,verifier_id=?,proof_type=?,
                       evidence_digest=?,key_algorithm=?,hardware_backed=?
                   WHERE user_id=? AND id=? AND status='pending' AND consumed_at IS NULL""",
                (
                    _iso(current),
                    device["id"],
                    proof.verifier_id,
                    proof.proof_type,
                    proof.evidence_digest,
                    proof.key_algorithm,
                    1 if proof.hardware_backed else 0,
                    user_id,
                    challenge_id,
                ),
            )
            if updated.rowcount != 1:
                # Defensive race handling: if another verifier consumed the one-time
                # challenge first, revoke the just-created identity immediately.
                self.security.revoke_device(user_id, device["id"])
                raise PermissionError("Aura Sec enrollment challenge was consumed concurrently")
        return {
            "enrolled": True,
            "device": device,
            "challenge_id": challenge_id,
            "protection_state": "awaiting_heartbeat",
            "proof": {
                "type": proof.proof_type,
                "verifier_id": proof.verifier_id,
                "evidence_digest": proof.evidence_digest,
                "key_algorithm": proof.key_algorithm,
                "hardware_backed": proof.hardware_backed,
            },
            "message": "Device identity enrolled. Protection is not healthy until a signed heartbeat is verified.",
        }


__all__ = [
    "AuraSecEnrollmentStore",
    "EnrollmentProofVerifier",
    "EnrollmentVerificationContext",
    "VerifiedEnrollmentProof",
]
