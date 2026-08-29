from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from .accounts import AccountStore
from .aura_sec_store import AuraSecStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Recovery timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_TARGET_TYPES = {"local_versioned", "external_drive", "cloud_encrypted", "immutable_cloud", "offline_export"}
_ALLOWED_TARGET_PROOF_TYPES = {"provider_connection", "local_target_possession", "storage_policy_attestation"}
_ALLOWED_KEY_ALGORITHMS = {"ed25519", "p256", "rsa-pss-sha256"}


@dataclass(frozen=True)
class RecoveryTargetVerificationContext:
    user_id: str
    device_id: str
    target_type: str
    display_name: str

    def evidence_payload(
        self,
        *,
        target_identity_digest: str,
        proof_type: str,
        encrypted: bool,
        isolated_or_immutable: bool,
    ) -> bytes:
        body = {
            "schema": "aura-sec-recovery-target-v1",
            "user_id": self.user_id,
            "device_id": self.device_id,
            "target_type": self.target_type,
            "display_name": self.display_name,
            "target_identity_digest": target_identity_digest,
            "proof_type": proof_type,
            "encrypted": bool(encrypted),
            "isolated_or_immutable": bool(isolated_or_immutable),
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class VerifiedBackupTargetConnection:
    target_identity_digest: str
    proof_type: str
    verifier_id: str
    evidence_digest: str
    encrypted: bool
    isolated_or_immutable: bool


BackupTargetVerifier = Callable[
    [dict, RecoveryTargetVerificationContext], VerifiedBackupTargetConnection | None
]


@dataclass(frozen=True)
class VerifiedRecoveryPointEvidence:
    public_key_fingerprint: str
    verifier_id: str
    key_algorithm: str
    evidence_digest: str
    malware_scan_state: str


RecoveryPointVerifier = Callable[
    [str, bytes, bytes], VerifiedRecoveryPointEvidence | None
]


@dataclass(frozen=True)
class VerifiedRestoreDrillEvidence:
    public_key_fingerprint: str
    verifier_id: str
    key_algorithm: str
    evidence_digest: str
    status: str
    integrity_reverified: bool
    restored_content_digest: str | None


RestoreDrillVerifier = Callable[
    [str, bytes, bytes], VerifiedRestoreDrillEvidence | None
]


class AuraSecRecoveryStore:
    """Evidence-backed ransomware/data-loss recovery state.

    A configured backup destination is never equivalent to proven recovery. Target
    connection properties come from a trusted provider/platform verifier; recovery-point
    integrity and restore-drill outcomes are signed by the currently enrolled device key.
    Provider credentials, raw attestation blobs and encryption root keys stay outside this
    database.
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
                CREATE TABLE IF NOT EXISTS aura_sec_backup_targets (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    target_identity_digest TEXT,
                    encrypted INTEGER NOT NULL,
                    isolated_or_immutable INTEGER NOT NULL,
                    connection_verifier_id TEXT,
                    connection_proof_type TEXT,
                    connection_evidence_digest TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    verified_at TEXT NOT NULL,
                    last_successful_backup_at TEXT,
                    last_integrity_check_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_recovery_points (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    integrity_verified INTEGER NOT NULL DEFAULT 0,
                    malware_scan_state TEXT NOT NULL DEFAULT 'not_scanned',
                    integrity_verifier_id TEXT,
                    integrity_key_algorithm TEXT,
                    integrity_evidence_digest TEXT,
                    verified_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES aura_sec_backup_targets(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_sec_restore_drills (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    recovery_point_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    integrity_reverified INTEGER NOT NULL DEFAULT 0,
                    restored_content_digest TEXT,
                    verifier_id TEXT,
                    verifier_key_algorithm TEXT,
                    verifier_evidence_digest TEXT,
                    notes TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(recovery_point_id) REFERENCES aura_sec_recovery_points(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_recovery_points_device_created
                ON aura_sec_recovery_points(device_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_aura_sec_restore_drills_device_completed
                ON aura_sec_restore_drills(device_id, completed_at);
                """
            )
            target_existing = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(aura_sec_backup_targets)").fetchall()
            }
            target_migrations = {
                "target_identity_digest": "TEXT",
                "connection_verifier_id": "TEXT",
                "connection_proof_type": "TEXT",
                "connection_evidence_digest": "TEXT",
            }
            for column, sql_type in target_migrations.items():
                if column not in target_existing:
                    con.execute(f"ALTER TABLE aura_sec_backup_targets ADD COLUMN {column} {sql_type}")

            point_existing = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(aura_sec_recovery_points)").fetchall()
            }
            point_migrations = {
                "integrity_verifier_id": "TEXT",
                "integrity_key_algorithm": "TEXT",
                "integrity_evidence_digest": "TEXT",
            }
            for column, sql_type in point_migrations.items():
                if column not in point_existing:
                    con.execute(f"ALTER TABLE aura_sec_recovery_points ADD COLUMN {column} {sql_type}")

            drill_existing = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(aura_sec_restore_drills)").fetchall()
            }
            drill_migrations = {
                "verifier_id": "TEXT",
                "verifier_key_algorithm": "TEXT",
                "verifier_evidence_digest": "TEXT",
            }
            for column, sql_type in drill_migrations.items():
                if column not in drill_existing:
                    con.execute(f"ALTER TABLE aura_sec_restore_drills ADD COLUMN {column} {sql_type}")

    @staticmethod
    def _digest(value: str, label: str) -> str:
        cleaned = (value or "").strip().lower()
        if not _HEX_256.fullmatch(cleaned):
            raise ValueError(f"{label} must be a SHA-256 hexadecimal value")
        return cleaned

    @staticmethod
    def _decode_signature(value: str, label: str) -> bytes:
        try:
            decoded = base64.b64decode((value or "").strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PermissionError(f"Aura Sec {label} signature is malformed") from exc
        if not 32 <= len(decoded) <= 4096:
            raise PermissionError(f"Aura Sec {label} signature length is invalid")
        return decoded

    def _device_identity(self, user_id: str, device_id: str) -> dict:
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required for recovery evidence")
        with self._connect() as con:
            row = con.execute(
                """SELECT id,public_key_fingerprint,status,revoked_at
                   FROM aura_sec_devices WHERE user_id=? AND id=?""",
                (user_id, device_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec device not found")
        item = dict(row)
        if item.get("status") == "revoked" or item.get("revoked_at"):
            raise PermissionError("Cannot configure or report recovery for a revoked device")
        fingerprint = str(item.get("public_key_fingerprint") or "").strip().lower()
        if not _HEX_256.fullmatch(fingerprint):
            raise PermissionError("Aura Sec recovery device key fingerprint is invalid")
        item["public_key_fingerprint"] = fingerprint
        return item

    @staticmethod
    def _validate_key_proof(
        proof,
        expected_type,
        *,
        expected_fingerprint: str,
        expected_digest: str,
        label: str,
    ) -> tuple[str, str, str]:
        if not isinstance(proof, expected_type):
            raise PermissionError(f"Aura Sec {label} evidence was not verified")
        fingerprint = (proof.public_key_fingerprint or "").strip().lower()
        verifier_id = (proof.verifier_id or "").strip()
        key_algorithm = (proof.key_algorithm or "").strip().lower()
        evidence_digest = (proof.evidence_digest or "").strip().lower()
        if not secrets.compare_digest(fingerprint, expected_fingerprint):
            raise PermissionError(f"Verified {label} key does not match enrolled device")
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError(f"Trusted {label} verifier identity is required")
        if key_algorithm not in _ALLOWED_KEY_ALGORITHMS:
            raise PermissionError(f"Unsupported {label} key algorithm")
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(
            evidence_digest, expected_digest
        ):
            raise PermissionError(f"Verified {label} evidence digest does not match canonical payload")
        return verifier_id, key_algorithm, evidence_digest

    def register_verified_target(
        self,
        user_id: str,
        device_id: str,
        *,
        target_type: str,
        display_name: str,
        verification_payload: dict,
        verifier: BackupTargetVerifier | None,
    ) -> dict:
        self._device_identity(user_id, device_id)
        kind = (target_type or "").strip().lower()
        if kind not in _ALLOWED_TARGET_TYPES:
            raise ValueError("Unsupported Aura Sec backup target type")
        name = (display_name or "").strip()[:160]
        if not name:
            raise ValueError("Backup target display name is required")
        if verifier is None:
            raise PermissionError("A trusted Aura Sec backup target verifier is required")
        context = RecoveryTargetVerificationContext(
            user_id=user_id,
            device_id=device_id,
            target_type=kind,
            display_name=name,
        )
        try:
            raw_proof = verifier(dict(verification_payload or {}), context)
        except Exception as exc:
            raise PermissionError("Aura Sec backup target verifier failed closed") from exc
        if not isinstance(raw_proof, VerifiedBackupTargetConnection):
            raise PermissionError("Aura Sec backup target connection was not verified")
        target_identity_digest = self._digest(raw_proof.target_identity_digest, "target_identity_digest")
        proof_type = (raw_proof.proof_type or "").strip().lower()
        verifier_id = (raw_proof.verifier_id or "").strip()
        evidence_digest = (raw_proof.evidence_digest or "").strip().lower()
        if proof_type not in _ALLOWED_TARGET_PROOF_TYPES:
            raise PermissionError("Unsupported Aura Sec backup target proof type")
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError("Trusted backup target verifier identity is required")
        if kind == "immutable_cloud" and not raw_proof.isolated_or_immutable:
            raise PermissionError("Immutable cloud target requires verified immutability/isolation")
        if kind in {"cloud_encrypted", "immutable_cloud"} and not raw_proof.encrypted:
            raise PermissionError("Cloud recovery target requires verified encryption")
        expected_digest = hashlib.sha256(
            context.evidence_payload(
                target_identity_digest=target_identity_digest,
                proof_type=proof_type,
                encrypted=bool(raw_proof.encrypted),
                isolated_or_immutable=bool(raw_proof.isolated_or_immutable),
            )
        ).hexdigest()
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(
            evidence_digest, expected_digest
        ):
            raise PermissionError("Verified backup target evidence digest does not match canonical payload")

        target_id = uuid4().hex
        now = _iso()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_backup_targets
                   (id,user_id,device_id,target_type,display_name,target_identity_digest,encrypted,
                    isolated_or_immutable,connection_verifier_id,connection_proof_type,
                    connection_evidence_digest,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    target_id,
                    user_id,
                    device_id,
                    kind,
                    name,
                    target_identity_digest,
                    int(bool(raw_proof.encrypted)),
                    int(bool(raw_proof.isolated_or_immutable)),
                    verifier_id,
                    proof_type,
                    evidence_digest,
                    now,
                ),
            )
        return self.get_target(user_id, target_id)

    def get_target(self, user_id: str, target_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_backup_targets WHERE user_id=? AND id=?",
                (user_id, target_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec backup target not found")
        item = dict(row)
        item["encrypted"] = bool(item["encrypted"])
        item["isolated_or_immutable"] = bool(item["isolated_or_immutable"])
        return item

    @staticmethod
    def _recovery_point_payload(
        *,
        user_id: str,
        device_id: str,
        target: dict,
        content_digest: str,
        manifest_digest: str,
        malware_scan_state: str,
        created_at: str,
    ) -> bytes:
        body = {
            "schema": "aura-sec-recovery-point-v1",
            "user_id": user_id,
            "device_id": device_id,
            "target_id": target["id"],
            "target_identity_digest": target.get("target_identity_digest"),
            "content_digest": content_digest,
            "manifest_digest": manifest_digest,
            "malware_scan_state": malware_scan_state,
            "created_at": created_at,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def record_verified_recovery_point(
        self,
        user_id: str,
        device_id: str,
        target_id: str,
        *,
        content_digest: str,
        manifest_digest: str,
        malware_scan_state: str,
        signature_b64: str,
        verifier: RecoveryPointVerifier | None,
        created_at: datetime | None = None,
    ) -> dict:
        identity = self._device_identity(user_id, device_id)
        target = self.get_target(user_id, target_id)
        if target["device_id"] != device_id or target["status"] != "active":
            raise PermissionError("Backup target is not active for this device")
        content_hash = self._digest(content_digest, "content digest")
        manifest_hash = self._digest(manifest_digest, "manifest digest")
        scan_state = (malware_scan_state or "").strip().lower()
        if scan_state not in {"clean", "suspicious", "infected", "not_scanned"}:
            raise ValueError("Invalid recovery point malware scan state")
        created = (created_at or _now()).astimezone(timezone.utc)
        created_text = _iso(created)
        payload = self._recovery_point_payload(
            user_id=user_id,
            device_id=device_id,
            target=target,
            content_digest=content_hash,
            manifest_digest=manifest_hash,
            malware_scan_state=scan_state,
            created_at=created_text,
        )
        if verifier is None:
            raise PermissionError("A trusted Aura Sec recovery-point verifier is required")
        signature = self._decode_signature(signature_b64, "recovery-point")
        try:
            proof = verifier(identity["public_key_fingerprint"], payload, signature)
        except Exception as exc:
            raise PermissionError("Aura Sec recovery-point verifier failed closed") from exc
        verifier_id, key_algorithm, evidence_digest = self._validate_key_proof(
            proof,
            VerifiedRecoveryPointEvidence,
            expected_fingerprint=identity["public_key_fingerprint"],
            expected_digest=hashlib.sha256(payload).hexdigest(),
            label="recovery-point",
        )
        proof_scan_state = (proof.malware_scan_state or "").strip().lower()
        if proof_scan_state != scan_state:
            raise PermissionError("Verified recovery-point malware scan state does not match payload")

        point_id = uuid4().hex
        verified = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_recovery_points
                   (id,user_id,device_id,target_id,created_at,content_digest,manifest_digest,
                    integrity_verified,malware_scan_state,integrity_verifier_id,integrity_key_algorithm,
                    integrity_evidence_digest,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    point_id,
                    user_id,
                    device_id,
                    target_id,
                    created_text,
                    content_hash,
                    manifest_hash,
                    1,
                    scan_state,
                    verifier_id,
                    key_algorithm,
                    evidence_digest,
                    _iso(verified),
                ),
            )
            con.execute(
                """UPDATE aura_sec_backup_targets
                   SET last_successful_backup_at=?,last_integrity_check_at=?
                   WHERE user_id=? AND id=?""",
                (created_text, _iso(verified), user_id, target_id),
            )
        return self.get_recovery_point(user_id, point_id)

    def get_recovery_point(self, user_id: str, point_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_recovery_points WHERE user_id=? AND id=?",
                (user_id, point_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec recovery point not found")
        item = dict(row)
        item["integrity_verified"] = bool(item["integrity_verified"])
        return item

    @staticmethod
    def _restore_payload(
        *,
        user_id: str,
        device_id: str,
        point: dict,
        status: str,
        restored_content_digest: str | None,
        completed_at: str,
    ) -> bytes:
        body = {
            "schema": "aura-sec-restore-drill-v1",
            "user_id": user_id,
            "device_id": device_id,
            "recovery_point_id": point["id"],
            "expected_content_digest": point["content_digest"],
            "status": status,
            "restored_content_digest": restored_content_digest,
            "completed_at": completed_at,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def record_restore_drill(
        self,
        user_id: str,
        device_id: str,
        recovery_point_id: str,
        *,
        status: str,
        restored_content_digest: str | None,
        signature_b64: str,
        verifier: RestoreDrillVerifier | None,
        notes: str = "",
        completed_at: datetime | None = None,
    ) -> dict:
        identity = self._device_identity(user_id, device_id)
        point = self.get_recovery_point(user_id, recovery_point_id)
        if point["device_id"] != device_id:
            raise PermissionError("Recovery point does not belong to this device")
        if not point.get("integrity_verified"):
            raise PermissionError("Restore drill requires a previously verified recovery point")
        state = (status or "").strip().lower()
        if state not in {"success", "failed"}:
            raise ValueError("Restore drill status must be success or failed")
        restored_hash = None
        if restored_content_digest:
            restored_hash = self._digest(restored_content_digest, "restored content digest")
        if state == "success" and restored_hash is None:
            raise ValueError("Successful restore drill requires restored content SHA-256")
        finished = (completed_at or _now()).astimezone(timezone.utc)
        finished_text = _iso(finished)
        payload = self._restore_payload(
            user_id=user_id,
            device_id=device_id,
            point=point,
            status=state,
            restored_content_digest=restored_hash,
            completed_at=finished_text,
        )
        if verifier is None:
            raise PermissionError("A trusted Aura Sec restore-drill verifier is required")
        signature = self._decode_signature(signature_b64, "restore-drill")
        try:
            proof = verifier(identity["public_key_fingerprint"], payload, signature)
        except Exception as exc:
            raise PermissionError("Aura Sec restore-drill verifier failed closed") from exc
        verifier_id, key_algorithm, evidence_digest = self._validate_key_proof(
            proof,
            VerifiedRestoreDrillEvidence,
            expected_fingerprint=identity["public_key_fingerprint"],
            expected_digest=hashlib.sha256(payload).hexdigest(),
            label="restore-drill",
        )
        proof_status = (proof.status or "").strip().lower()
        proof_digest = (proof.restored_content_digest or "").strip().lower() or None
        if proof_status != state or proof_digest != restored_hash:
            raise PermissionError("Verified restore-drill outcome does not match canonical payload")
        if state == "success":
            if not proof.integrity_reverified:
                raise PermissionError("Successful restore drill requires verified post-restore integrity")
            if restored_hash != point["content_digest"].lower():
                raise PermissionError("Verified restored content digest does not match recovery point")

        drill_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_sec_restore_drills
                   (id,user_id,device_id,recovery_point_id,started_at,completed_at,status,
                    integrity_reverified,restored_content_digest,verifier_id,verifier_key_algorithm,
                    verifier_evidence_digest,notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    drill_id,
                    user_id,
                    device_id,
                    recovery_point_id,
                    finished_text,
                    finished_text,
                    state,
                    int(bool(proof.integrity_reverified)),
                    restored_hash,
                    verifier_id,
                    key_algorithm,
                    evidence_digest,
                    (notes or "").strip()[:1000],
                ),
            )
        return self.get_restore_drill(user_id, drill_id)

    def get_restore_drill(self, user_id: str, drill_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_restore_drills WHERE user_id=? AND id=?",
                (user_id, drill_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec restore drill not found")
        item = dict(row)
        item["integrity_reverified"] = bool(item["integrity_reverified"])
        return item

    def readiness(
        self,
        user_id: str,
        device_id: str,
        *,
        now: datetime | None = None,
        max_recovery_point_age: timedelta = timedelta(days=1),
        max_restore_test_age: timedelta = timedelta(days=30),
    ) -> dict:
        current = (now or _now()).astimezone(timezone.utc)
        with self._connect() as con:
            target_rows = con.execute(
                """SELECT * FROM aura_sec_backup_targets
                   WHERE user_id=? AND device_id=? AND status='active' AND revoked_at IS NULL""",
                (user_id, device_id),
            ).fetchall()
            point_row = con.execute(
                """SELECT * FROM aura_sec_recovery_points
                   WHERE user_id=? AND device_id=? AND integrity_verified=1
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, device_id),
            ).fetchone()
            drill_row = con.execute(
                """SELECT * FROM aura_sec_restore_drills
                   WHERE user_id=? AND device_id=? AND status='success' AND integrity_reverified=1
                   ORDER BY completed_at DESC LIMIT 1""",
                (user_id, device_id),
            ).fetchone()

        targets = [dict(row) for row in target_rows]
        latest_point = dict(point_row) if point_row else None
        latest_drill = dict(drill_row) if drill_row else None
        encrypted_target = any(bool(row["encrypted"]) for row in targets)
        isolated_target = any(bool(row["isolated_or_immutable"]) for row in targets)
        point_fresh = bool(
            latest_point and current - _parse(latest_point["created_at"]) <= max_recovery_point_age
        )
        point_clean = bool(latest_point and latest_point["malware_scan_state"] == "clean")
        drill_fresh = bool(
            latest_drill and current - _parse(latest_drill["completed_at"]) <= max_restore_test_age
        )

        if not targets:
            state = "not_configured"
        elif not latest_point:
            state = "no_verified_recovery_point"
        elif not point_fresh:
            state = "recovery_point_stale"
        elif not latest_drill or not drill_fresh:
            state = "restore_test_required"
        elif not point_clean:
            state = "recovery_point_needs_security_review"
        elif encrypted_target and isolated_target:
            state = "ransomware_ready"
        else:
            state = "recovery_verified"

        return {
            "state": state,
            "active_targets": len(targets),
            "encrypted_target_present": encrypted_target,
            "isolated_or_immutable_target_present": isolated_target,
            "latest_recovery_point": None
            if not latest_point
            else {
                "id": latest_point["id"],
                "created_at": latest_point["created_at"],
                "malware_scan_state": latest_point["malware_scan_state"],
                "fresh": point_fresh,
            },
            "latest_restore_drill": None
            if not latest_drill
            else {
                "id": latest_drill["id"],
                "completed_at": latest_drill["completed_at"],
                "fresh": drill_fresh,
            },
            "restore_proven": bool(latest_drill and drill_fresh),
            "truth": "Backup configuration alone never counts as proven recovery; readiness requires verifier-backed integrity evidence and a recent signed successful restore drill.",
        }


__all__ = [
    "AuraSecRecoveryStore",
    "BackupTargetVerifier",
    "RecoveryPointVerifier",
    "RecoveryTargetVerificationContext",
    "RestoreDrillVerifier",
    "VerifiedBackupTargetConnection",
    "VerifiedRecoveryPointEvidence",
    "VerifiedRestoreDrillEvidence",
]
