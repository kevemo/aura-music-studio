from __future__ import annotations

import base64
import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_protocol import ActionRisk, ActionType
from .aura_sec_store import AuraSecStore

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_KEY_ALGORITHMS = {"ed25519", "p256", "rsa-pss-sha256"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CredentialRotationContext:
    user_id: str
    device_id: str
    approved_action_id: str
    challenge_id: str
    challenge: str
    old_public_key_fingerprint: str
    new_public_key_fingerprint: str
    platform: str
    architecture: str
    created_at: str
    expires_at: str

    def signed_payload(self) -> bytes:
        parts = (
            self.user_id,
            self.device_id,
            self.approved_action_id,
            self.challenge_id,
            self.challenge,
            self.old_public_key_fingerprint,
            self.new_public_key_fingerprint,
            self.platform,
            self.architecture,
            self.created_at,
            self.expires_at,
        )
        if any("\n" in part or "\r" in part for part in parts):
            raise ValueError("Credential rotation canonical fields must not contain newlines")
        return (
            "AURA-SEC-CREDENTIAL-ROTATION-V1\n" + "\n".join(parts) + "\n"
        ).encode("utf-8")


@dataclass(frozen=True)
class VerifiedCredentialRotation:
    old_public_key_fingerprint: str
    new_public_key_fingerprint: str
    verifier_id: str
    old_key_algorithm: str
    new_key_algorithm: str
    evidence_digest: str
    new_key_hardware_backed: bool = False


CredentialRotationVerifier = Callable[
    [str, str, bytes, bytes, bytes], VerifiedCredentialRotation | None
]


class AuraSecDeviceCredentialRotation:
    """Native-only dual-key device credential rotation.

    The member approval gateway owns human authorization. This service accepts only a
    previously approved ``rotate_device_credential`` action, then proves authority from
    both the current enrolled key and the proposed replacement key over one canonical,
    one-time challenge. No private key material is persisted here.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
    ) -> None:
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
                CREATE TABLE IF NOT EXISTS aura_sec_device_key_rotations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    approved_action_id TEXT NOT NULL,
                    challenge_hash TEXT NOT NULL UNIQUE,
                    old_public_key_fingerprint TEXT NOT NULL,
                    new_public_key_fingerprint TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    verifier_id TEXT,
                    old_key_algorithm TEXT,
                    new_key_algorithm TEXT,
                    evidence_digest TEXT,
                    new_key_hardware_backed INTEGER,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(approved_action_id) REFERENCES aura_sec_actions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_key_rotation_device
                ON aura_sec_device_key_rotations(user_id,device_id,status,expires_at);
                """
            )

    def _device_identity(self, user_id: str, device_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT id,user_id,platform,architecture,public_key_fingerprint,
                          status,revoked_at
                   FROM aura_sec_devices WHERE user_id=? AND id=?""",
                (user_id, device_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec device not found")
        item = dict(row)
        fingerprint = str(item.get("public_key_fingerprint") or "").strip().lower()
        if not _HEX_256.fullmatch(fingerprint):
            raise PermissionError("Aura Sec enrolled device key fingerprint is invalid")
        item["public_key_fingerprint"] = fingerprint
        return item

    def _approved_rotation_action(
        self,
        user_id: str,
        device_id: str,
        action_id: str,
    ) -> dict:
        action = self.security.get_action(user_id, action_id)
        if action.get("device_id") != device_id:
            raise PermissionError(
                "Credential rotation approval is bound to a different device"
            )
        if action.get("action_type") != ActionType.ROTATE_DEVICE_CREDENTIAL.value:
            raise PermissionError("Approved action is not a device credential rotation")
        if action.get("risk_class") != ActionRisk.STRONG_REAUTH_REQUIRED.value:
            raise PermissionError(
                "Device credential rotation must retain strong re-authentication risk"
            )
        if action.get("status") != "approved":
            raise PermissionError(
                "Device credential rotation requires a previously approved action"
            )
        return action

    def create_challenge(
        self,
        user_id: str,
        device_id: str,
        *,
        approved_action_id: str,
        new_public_key_fingerprint: str,
        ttl_seconds: int = 300,
    ) -> dict:
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError(
                "Active Aura Sec licence required for credential rotation"
            )
        device = self._device_identity(user_id, device_id)
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot rotate credentials")
        self._approved_rotation_action(user_id, device_id, approved_action_id)
        if not 60 <= int(ttl_seconds) <= 600:
            raise ValueError(
                "Credential rotation challenge lifetime must be between 60 and 600 seconds"
            )

        new_fingerprint = (new_public_key_fingerprint or "").strip().lower()
        if not _HEX_256.fullmatch(new_fingerprint):
            raise ValueError(
                "New device public-key fingerprint must be a SHA-256 hex digest"
            )
        if secrets.compare_digest(
            new_fingerprint, device["public_key_fingerprint"]
        ):
            raise ValueError(
                "Replacement device key must differ from the currently enrolled key"
            )
        with self._connect() as con:
            duplicate = con.execute(
                """SELECT id FROM aura_sec_devices
                   WHERE user_id=? AND public_key_fingerprint=? AND id<>?""",
                (user_id, new_fingerprint, device_id),
            ).fetchone()
        if duplicate:
            raise ValueError("Replacement device key is already enrolled")

        now = _now()
        expires = now + timedelta(seconds=int(ttl_seconds))
        challenge = secrets.token_urlsafe(32)
        challenge_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """UPDATE aura_sec_device_key_rotations
                   SET status='superseded',attempt_id=NULL
                   WHERE user_id=? AND device_id=?
                     AND status IN ('pending','verifying')""",
                (user_id, device_id),
            )
            con.execute(
                """INSERT INTO aura_sec_device_key_rotations
                   (id,user_id,device_id,approved_action_id,challenge_hash,
                    old_public_key_fingerprint,new_public_key_fingerprint,
                    platform,architecture,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                (
                    challenge_id,
                    user_id,
                    device_id,
                    approved_action_id,
                    _hash_secret(challenge),
                    device["public_key_fingerprint"],
                    new_fingerprint,
                    str(device["platform"]),
                    str(device["architecture"]),
                    _iso(now),
                    _iso(expires),
                ),
            )
        return {
            "challenge_id": challenge_id,
            "challenge": challenge,
            "expires_at": _iso(expires),
            "old_public_key_fingerprint": device["public_key_fingerprint"],
            "new_public_key_fingerprint": new_fingerprint,
            "approved_action_id": approved_action_id,
            "one_time": True,
            "member_browser_route_exposed": False,
        }

    def _reserve(
        self,
        user_id: str,
        challenge_id: str,
        challenge: str,
        *,
        now: datetime,
    ) -> tuple[dict, str]:
        attempt_id = uuid4().hex
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM aura_sec_device_key_rotations
                   WHERE user_id=? AND id=? AND challenge_hash=?""",
                (user_id, challenge_id, _hash_secret(challenge)),
            ).fetchone()
            if not row:
                raise PermissionError(
                    "Aura Sec credential rotation challenge is invalid"
                )
            item = dict(row)
            if item.get("status") != "pending":
                raise PermissionError(
                    "Aura Sec credential rotation challenge is no longer pending"
                )
            expires = datetime.fromisoformat(item["expires_at"]).astimezone(timezone.utc)
            if now >= expires:
                con.execute(
                    """UPDATE aura_sec_device_key_rotations
                       SET status='expired',attempt_id=NULL WHERE id=?""",
                    (challenge_id,),
                )
                raise PermissionError(
                    "Aura Sec credential rotation challenge has expired"
                )
            cursor = con.execute(
                """UPDATE aura_sec_device_key_rotations
                   SET status='verifying',attempt_id=?
                   WHERE id=? AND status='pending'""",
                (attempt_id, challenge_id),
            )
            if cursor.rowcount != 1:
                raise PermissionError(
                    "Aura Sec credential rotation challenge was concurrently claimed"
                )
        return item, attempt_id

    def _release_failed_attempt(self, challenge_id: str, attempt_id: str) -> None:
        current = _now()
        with self._connect() as con:
            row = con.execute(
                """SELECT expires_at FROM aura_sec_device_key_rotations
                   WHERE id=? AND status='verifying' AND attempt_id=?""",
                (challenge_id, attempt_id),
            ).fetchone()
            if not row:
                return
            expires = datetime.fromisoformat(row["expires_at"]).astimezone(timezone.utc)
            status = "expired" if current >= expires else "pending"
            con.execute(
                """UPDATE aura_sec_device_key_rotations
                   SET status=?,attempt_id=NULL
                   WHERE id=? AND status='verifying' AND attempt_id=?""",
                (status, challenge_id, attempt_id),
            )

    @staticmethod
    def _decode_signature(value: str, label: str) -> bytes:
        try:
            decoded = base64.b64decode((value or "").strip(), validate=True)
        except Exception as exc:
            raise PermissionError(
                f"Aura Sec {label} signature is not valid base64"
            ) from exc
        if not 32 <= len(decoded) <= 1024:
            raise PermissionError(f"Aura Sec {label} signature length is invalid")
        return decoded

    def _verify(
        self,
        item: dict,
        challenge: str,
        *,
        old_signature_b64: str,
        new_signature_b64: str,
        verifier: CredentialRotationVerifier | None,
    ) -> VerifiedCredentialRotation:
        if verifier is None:
            raise PermissionError(
                "A trusted Aura Sec credential rotation verifier is required"
            )
        context = CredentialRotationContext(
            user_id=str(item["user_id"]),
            device_id=str(item["device_id"]),
            approved_action_id=str(item["approved_action_id"]),
            challenge_id=str(item["id"]),
            challenge=challenge,
            old_public_key_fingerprint=str(item["old_public_key_fingerprint"]),
            new_public_key_fingerprint=str(item["new_public_key_fingerprint"]),
            platform=str(item["platform"]),
            architecture=str(item["architecture"]),
            created_at=str(item["created_at"]),
            expires_at=str(item["expires_at"]),
        )
        payload = context.signed_payload()
        old_signature = self._decode_signature(old_signature_b64, "current-key")
        new_signature = self._decode_signature(new_signature_b64, "replacement-key")
        try:
            proof = verifier(
                context.old_public_key_fingerprint,
                context.new_public_key_fingerprint,
                payload,
                old_signature,
                new_signature,
            )
        except Exception as exc:
            raise PermissionError(
                "Aura Sec credential rotation verification failed closed"
            ) from exc
        if not isinstance(proof, VerifiedCredentialRotation):
            raise PermissionError(
                "Aura Sec credential rotation dual-key proof was not verified"
            )

        old_fp = (proof.old_public_key_fingerprint or "").strip().lower()
        new_fp = (proof.new_public_key_fingerprint or "").strip().lower()
        verifier_id = (proof.verifier_id or "").strip()
        old_alg = (proof.old_key_algorithm or "").strip().lower()
        new_alg = (proof.new_key_algorithm or "").strip().lower()
        evidence_digest = (proof.evidence_digest or "").strip().lower()
        if not secrets.compare_digest(
            old_fp, context.old_public_key_fingerprint
        ):
            raise PermissionError(
                "Credential rotation proof does not match the currently enrolled key"
            )
        if not secrets.compare_digest(
            new_fp, context.new_public_key_fingerprint
        ):
            raise PermissionError(
                "Credential rotation proof does not match the replacement key"
            )
        expected_digest = hashlib.sha256(payload).hexdigest()
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(
            evidence_digest, expected_digest
        ):
            raise PermissionError(
                "Credential rotation evidence digest does not match the signed payload"
            )
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError(
                "Trusted credential rotation verifier identity is required"
            )
        if (
            old_alg not in _ALLOWED_KEY_ALGORITHMS
            or new_alg not in _ALLOWED_KEY_ALGORITHMS
        ):
            raise PermissionError("Unsupported Aura Sec device key algorithm")
        return VerifiedCredentialRotation(
            old_public_key_fingerprint=old_fp,
            new_public_key_fingerprint=new_fp,
            verifier_id=verifier_id,
            old_key_algorithm=old_alg,
            new_key_algorithm=new_alg,
            evidence_digest=evidence_digest,
            new_key_hardware_backed=bool(proof.new_key_hardware_backed),
        )

    @staticmethod
    def _table_exists(con: sqlite3.Connection, table_name: str) -> bool:
        return (
            con.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name=?""",
                (table_name,),
            ).fetchone()
            is not None
        )

    def complete_rotation(
        self,
        user_id: str,
        challenge_id: str,
        *,
        challenge: str,
        old_signature_b64: str,
        new_signature_b64: str,
        verifier: CredentialRotationVerifier | None,
        now: datetime | None = None,
    ) -> dict:
        current = (now or _now()).astimezone(timezone.utc)
        item, attempt_id = self._reserve(
            user_id, challenge_id, challenge, now=current
        )
        try:
            if self.security.licence(user_id).get("status") != "active":
                raise PermissionError(
                    "Active Aura Sec licence required for credential rotation"
                )
            device = self._device_identity(user_id, str(item["device_id"]))
            if device.get("status") == "revoked" or device.get("revoked_at"):
                raise PermissionError(
                    "Revoked Aura Sec device cannot rotate credentials"
                )
            if not secrets.compare_digest(
                device["public_key_fingerprint"],
                str(item["old_public_key_fingerprint"]),
            ):
                raise PermissionError(
                    "Aura Sec device credential changed after this challenge was issued"
                )
            self._approved_rotation_action(
                user_id,
                str(item["device_id"]),
                str(item["approved_action_id"]),
            )
            proof = self._verify(
                item,
                challenge,
                old_signature_b64=old_signature_b64,
                new_signature_b64=new_signature_b64,
                verifier=verifier,
            )
            completed_at = _iso(current)
            with self._connect() as con:
                device_update = con.execute(
                    """UPDATE aura_sec_devices
                       SET public_key_fingerprint=?,
                           protection_state='awaiting_heartbeat',
                           last_seen_at=NULL,last_policy_version=NULL,
                           last_report_digest=NULL,last_heartbeat_sequence=0,
                           last_heartbeat_verifier_id=NULL,
                           last_heartbeat_key_algorithm=NULL,
                           last_heartbeat_evidence_digest=NULL
                       WHERE user_id=? AND id=?
                         AND public_key_fingerprint=? AND revoked_at IS NULL""",
                    (
                        proof.new_public_key_fingerprint,
                        user_id,
                        item["device_id"],
                        item["old_public_key_fingerprint"],
                    ),
                )
                if device_update.rowcount != 1:
                    raise PermissionError(
                        "Aura Sec device credential changed concurrently"
                    )

                if self._table_exists(con, "aura_sec_native_poll_state"):
                    con.execute(
                        """DELETE FROM aura_sec_native_poll_state
                           WHERE user_id=? AND device_id=?""",
                        (user_id, item["device_id"]),
                    )
                if self._table_exists(con, "aura_sec_heartbeat_challenges"):
                    con.execute(
                        """UPDATE aura_sec_heartbeat_challenges
                           SET status='superseded',attempt_id=NULL
                           WHERE user_id=? AND device_id=?
                             AND status IN ('pending','verifying')""",
                        (user_id, item["device_id"]),
                    )

                action_update = con.execute(
                    """UPDATE aura_sec_actions
                       SET status='verified',executed_at=?,verified_at=?
                       WHERE id=? AND user_id=? AND device_id=?
                         AND action_type=? AND status='approved'""",
                    (
                        completed_at,
                        completed_at,
                        item["approved_action_id"],
                        user_id,
                        item["device_id"],
                        ActionType.ROTATE_DEVICE_CREDENTIAL.value,
                    ),
                )
                if action_update.rowcount != 1:
                    raise PermissionError(
                        "Aura Sec credential rotation approval changed concurrently"
                    )

                rotation_update = con.execute(
                    """UPDATE aura_sec_device_key_rotations
                       SET status='consumed',attempt_id=NULL,consumed_at=?,
                           verifier_id=?,old_key_algorithm=?,new_key_algorithm=?,
                           evidence_digest=?,new_key_hardware_backed=?
                       WHERE id=? AND user_id=? AND status='verifying'
                         AND attempt_id=?""",
                    (
                        completed_at,
                        proof.verifier_id,
                        proof.old_key_algorithm,
                        proof.new_key_algorithm,
                        proof.evidence_digest,
                        1 if proof.new_key_hardware_backed else 0,
                        challenge_id,
                        user_id,
                        attempt_id,
                    ),
                )
                if rotation_update.rowcount != 1:
                    raise RuntimeError(
                        "Aura Sec credential rotation completion failed closed"
                    )
        except Exception:
            self._release_failed_attempt(challenge_id, attempt_id)
            raise

        return {
            "device": self.security.get_device(
                user_id, str(item["device_id"])
            ),
            "rotation_consumed": True,
            "approved_action_id": item["approved_action_id"],
            "action_status": "verified",
            "verification": {
                "verifier_id": proof.verifier_id,
                "old_key_algorithm": proof.old_key_algorithm,
                "new_key_algorithm": proof.new_key_algorithm,
                "evidence_digest": proof.evidence_digest,
                "new_key_hardware_backed": proof.new_key_hardware_backed,
            },
            "old_credential_invalidated": True,
            "fresh_heartbeat_required": True,
            "member_browser_route_exposed": False,
        }


__all__ = [
    "AuraSecDeviceCredentialRotation",
    "CredentialRotationContext",
    "CredentialRotationVerifier",
    "VerifiedCredentialRotation",
]
