from __future__ import annotations

import base64
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
from .aura_sec_approval_lifetime import AuraSecApprovalLifetime
from .aura_sec_protocol import ActionRisk, ActionType, CommandReceipt, SecurityCommand
from .aura_sec_store import AuraSecStore

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_KEY_ALGORITHMS = {"ed25519", "p256", "rsa-pss-sha256"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_nonce(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_command_receipt_payload(receipt: CommandReceipt) -> bytes:
    """Return the deterministic bytes that a native device signs for a command receipt."""
    payload = {
        "command_id": receipt.command_id,
        "detail": receipt.detail,
        "device_id": receipt.device_id,
        "evidence_digest": receipt.evidence_digest.lower() if receipt.evidence_digest else None,
        "occurred_at": receipt.occurred_at.astimezone(timezone.utc).isoformat(),
        "result_code": receipt.result_code,
        "schema_version": receipt.schema_version,
        "status": receipt.status,
    }
    return (
        "AURA-SEC-COMMAND-RECEIPT-V1\n"
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True)
class VerifiedCommandReceiptSignature:
    public_key_fingerprint: str
    verifier_id: str
    key_algorithm: str
    evidence_digest: str


CommandReceiptSignatureVerifier = Callable[
    [str, bytes, bytes], VerifiedCommandReceiptSignature | None
]


_ALLOWED_RECEIPT_TRANSITIONS: dict[str, set[str]] = {
    "issued": {"received", "rejected", "failed"},
    "received": {"executed", "failed"},
    "executed": {"verified", "failed"},
    "rejected": set(),
    "failed": set(),
    "verified": set(),
}


class AuraSecCommandStore:
    """Persist short-lived typed endpoint commands and cryptographically verified receipts."""

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
        approvals: AuraSecApprovalLifetime | None = None,
    ):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)
        self.approvals = approvals or AuraSecApprovalLifetime(self.accounts, self.security)
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
                CREATE TABLE IF NOT EXISTS aura_sec_commands (
                    id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'issued',
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_receipt_at TEXT,
                    result_code TEXT,
                    evidence_digest TEXT,
                    last_receipt_verifier TEXT,
                    last_receipt_key_algorithm TEXT,
                    last_receipt_payload_digest TEXT,
                    last_receipt_signature_digest TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(device_id, nonce_hash),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE,
                    FOREIGN KEY(action_id) REFERENCES aura_sec_actions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_commands_device_status
                ON aura_sec_commands(device_id, status, expires_at);
                """
            )
            existing = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(aura_sec_commands)").fetchall()
            }
            migrations = {
                "last_receipt_verifier": "TEXT",
                "last_receipt_key_algorithm": "TEXT",
                "last_receipt_payload_digest": "TEXT",
                "last_receipt_signature_digest": "TEXT",
            }
            for column, sql_type in migrations.items():
                if column not in existing:
                    con.execute(f"ALTER TABLE aura_sec_commands ADD COLUMN {column} {sql_type}")

    def _device_identity(self, user_id: str, device_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT public_key_fingerprint,status,revoked_at
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

    @staticmethod
    def _decode_signature(signature_b64: str) -> bytes:
        try:
            signature = base64.b64decode((signature_b64 or "").strip(), validate=True)
        except Exception as exc:
            raise PermissionError("Aura Sec command receipt signature is not valid base64") from exc
        if not 32 <= len(signature) <= 1024:
            raise PermissionError("Aura Sec command receipt signature length is invalid")
        return signature

    def _verify_receipt_signature(
        self,
        user_id: str,
        receipt: CommandReceipt,
        *,
        signature_b64: str,
        signature_verifier: CommandReceiptSignatureVerifier | None,
    ) -> VerifiedCommandReceiptSignature:
        if signature_verifier is None:
            raise PermissionError("A trusted Aura Sec command receipt verifier is required")
        device = self._device_identity(user_id, receipt.device_id)
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot submit command receipts")
        payload = canonical_command_receipt_payload(receipt)
        signature = self._decode_signature(signature_b64)
        try:
            proof = signature_verifier(device["public_key_fingerprint"], payload, signature)
        except Exception as exc:
            raise PermissionError("Aura Sec command receipt verification failed closed") from exc
        if not isinstance(proof, VerifiedCommandReceiptSignature):
            raise PermissionError("Aura Sec command receipt signature was not verified")

        fingerprint = (proof.public_key_fingerprint or "").strip().lower()
        verifier_id = (proof.verifier_id or "").strip()
        key_algorithm = (proof.key_algorithm or "").strip().lower()
        evidence_digest = (proof.evidence_digest or "").strip().lower()
        if not secrets.compare_digest(fingerprint, device["public_key_fingerprint"]):
            raise PermissionError("Aura Sec command receipt verifier returned the wrong device key")
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError("Trusted Aura Sec command receipt verifier identity is required")
        if key_algorithm not in _ALLOWED_KEY_ALGORITHMS:
            raise PermissionError("Unsupported Aura Sec command receipt key algorithm")
        expected_digest = hashlib.sha256(payload).hexdigest()
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(
            evidence_digest, expected_digest
        ):
            raise PermissionError("Aura Sec command receipt evidence digest does not match the signed payload")
        return VerifiedCommandReceiptSignature(
            public_key_fingerprint=fingerprint,
            verifier_id=verifier_id,
            key_algorithm=key_algorithm,
            evidence_digest=evidence_digest,
        )

    def issue_approved_action(
        self,
        user_id: str,
        action_id: str,
        *,
        policy_version: str,
        nonce: str,
        parameters: dict | None = None,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> SecurityCommand:
        if not 30 <= int(ttl_seconds) <= 900:
            raise ValueError("Aura Sec command lifetime must be between 30 and 900 seconds")

        issued = (now or _now()).astimezone(timezone.utc)
        action = self.approvals.require_fresh(
            user_id,
            action_id,
            now=issued,
            minimum_remaining_seconds=30,
        )
        device = self.security.get_device(user_id, action["device_id"])
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Cannot issue Aura Sec command to a revoked device")
        try:
            action_type = ActionType(action["action_type"])
            risk = ActionRisk(action["risk_class"])
        except ValueError as exc:
            raise ValueError("Stored Aura Sec action is not compatible with the bounded command protocol") from exc

        approval_expires_at = datetime.fromisoformat(action["approval_expires_at"]).astimezone(timezone.utc)
        requested_expiry = issued + timedelta(seconds=int(ttl_seconds))
        command_expiry = min(requested_expiry, approval_expires_at)
        if (command_expiry - issued).total_seconds() < 30:
            self.approvals.require_fresh(
                user_id,
                action_id,
                now=issued,
                minimum_remaining_seconds=30,
            )
            raise PermissionError("Aura Sec approval is too close to expiry for a safe command lifetime")

        approval_id = action_id if risk in {
            ActionRisk.CONFIRMATION_REQUIRED,
            ActionRisk.STRONG_REAUTH_REQUIRED,
        } else None
        command = SecurityCommand(
            command_id=uuid4().hex,
            device_id=action["device_id"],
            action=action_type,
            risk=risk,
            issued_at=issued,
            expires_at=command_expiry,
            policy_version=(policy_version or "").strip(),
            nonce=(nonce or "").strip(),
            approval_id=approval_id,
            parameters=parameters or {},
        )
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO aura_sec_commands
                       (id,action_id,user_id,device_id,action_type,risk_class,policy_version,nonce_hash,
                        parameters_json,status,issued_at,expires_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'issued',?,?,?)""",
                    (
                        command.command_id,
                        action_id,
                        user_id,
                        command.device_id,
                        command.action.value,
                        command.risk.value,
                        command.policy_version,
                        _hash_nonce(command.nonce),
                        json.dumps(command.parameters, separators=(",", ":"), ensure_ascii=False),
                        command.issued_at.isoformat(),
                        command.expires_at.isoformat(),
                        _iso(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Aura Sec action was already issued or nonce was replayed") from exc
        return command

    def get(self, user_id: str, command_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_sec_commands WHERE user_id=? AND id=?",
                (user_id, command_id),
            ).fetchone()
        if not row:
            raise ValueError("Aura Sec command not found")
        item = dict(row)
        try:
            item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
        except json.JSONDecodeError:
            item["parameters"] = {}
        item.pop("nonce_hash", None)
        return item

    def accept_verified_receipt(
        self,
        user_id: str,
        receipt: CommandReceipt,
        *,
        signature_b64: str,
        signature_verifier: CommandReceiptSignatureVerifier | None,
        now: datetime | None = None,
    ) -> dict:
        command = self.get(user_id, receipt.command_id)
        if command["device_id"] != receipt.device_id:
            raise PermissionError("Aura Sec receipt device does not match command target")

        current = (now or _now()).astimezone(timezone.utc)
        occurred = receipt.occurred_at.astimezone(timezone.utc)
        issued = datetime.fromisoformat(command["issued_at"]).astimezone(timezone.utc)
        expiry = datetime.fromisoformat(command["expires_at"]).astimezone(timezone.utc)
        if occurred < issued - timedelta(seconds=120):
            raise PermissionError("Aura Sec command receipt predates command issuance")
        if occurred > current + timedelta(seconds=120):
            raise PermissionError("Aura Sec command receipt timestamp is too far in the future")

        verification = self._verify_receipt_signature(
            user_id,
            receipt,
            signature_b64=signature_b64,
            signature_verifier=signature_verifier,
        )
        receipt_payload_digest = verification.evidence_digest
        receipt_signature_digest = hashlib.sha256(
            self._decode_signature(signature_b64)
        ).hexdigest()

        duplicate = False
        with self._connect() as con:
            latest = con.execute(
                "SELECT * FROM aura_sec_commands WHERE user_id=? AND id=?",
                (user_id, receipt.command_id),
            ).fetchone()
            if not latest:
                raise ValueError("Aura Sec command not found")
            latest_status = str(latest["status"])

            if receipt.status == latest_status:
                stored_payload_digest = str(latest["last_receipt_payload_digest"] or "").strip().lower()
                if stored_payload_digest and secrets.compare_digest(
                    stored_payload_digest, receipt_payload_digest
                ):
                    duplicate = True
                else:
                    raise ValueError(
                        "Conflicting Aura Sec command receipt for the current lifecycle state"
                    )
            else:
                if receipt.status not in _ALLOWED_RECEIPT_TRANSITIONS.get(latest_status, set()):
                    raise ValueError(
                        f"Invalid Aura Sec command transition {latest_status} -> {receipt.status}"
                    )
                if (current > expiry or occurred > expiry) and receipt.status not in {
                    "failed",
                    "rejected",
                }:
                    raise PermissionError(
                        "Expired Aura Sec command cannot report a new successful state"
                    )

                updated = con.execute(
                    """UPDATE aura_sec_commands
                       SET status=?,last_receipt_at=?,result_code=?,evidence_digest=?,
                           last_receipt_verifier=?,last_receipt_key_algorithm=?,
                           last_receipt_payload_digest=?,last_receipt_signature_digest=?,updated_at=?
                       WHERE user_id=? AND id=? AND status=?""",
                    (
                        receipt.status,
                        occurred.isoformat(),
                        receipt.result_code,
                        receipt.evidence_digest.lower() if receipt.evidence_digest else None,
                        verification.verifier_id,
                        verification.key_algorithm,
                        receipt_payload_digest,
                        receipt_signature_digest,
                        _iso(current),
                        user_id,
                        receipt.command_id,
                        latest_status,
                    ),
                )
                if updated.rowcount != 1:
                    raise PermissionError("Aura Sec command receipt state changed concurrently")

                if receipt.status == "executed":
                    action_update = con.execute(
                        """UPDATE aura_sec_actions SET status='executed',executed_at=?
                           WHERE user_id=? AND id=? AND status='approved'""",
                        (occurred.isoformat(), user_id, command["action_id"]),
                    )
                    if action_update.rowcount != 1:
                        raise PermissionError("Aura Sec action state changed before execution receipt")
                elif receipt.status == "verified":
                    action_update = con.execute(
                        """UPDATE aura_sec_actions SET status='verified',verified_at=?
                           WHERE user_id=? AND id=? AND status='executed'""",
                        (occurred.isoformat(), user_id, command["action_id"]),
                    )
                    if action_update.rowcount != 1:
                        raise PermissionError("Aura Sec action state changed before verification receipt")
                elif receipt.status in {"failed", "rejected"}:
                    allowed_action_states = ("approved", "executed")
                    placeholders = ",".join("?" for _ in allowed_action_states)
                    action_update = con.execute(
                        f"""UPDATE aura_sec_actions SET status=?
                            WHERE user_id=? AND id=? AND status IN ({placeholders})""",
                        (receipt.status, user_id, command["action_id"], *allowed_action_states),
                    )
                    if action_update.rowcount != 1:
                        raise PermissionError("Aura Sec action state changed before failure receipt")

        return self.get(user_id, receipt.command_id)


__all__ = [
    "AuraSecCommandStore",
    "CommandReceiptSignatureVerifier",
    "VerifiedCommandReceiptSignature",
    "canonical_command_receipt_payload",
]
