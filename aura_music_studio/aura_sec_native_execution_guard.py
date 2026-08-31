from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .aura_sec_command_signing import (
    SignedSecurityCommand,
    VerifiedServerCommandSignature,
    verify_signed_security_command,
)

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CODE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_FINAL_STATES = {"completed", "failed"}


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec execution timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return _utc(value).isoformat()


def _signed_envelope_digest(command: SignedSecurityCommand) -> str:
    """Bind idempotency to the complete authenticated envelope, not command_id alone."""
    payload = json.dumps(
        command.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class NativeExecutionReservation:
    command_id: str
    device_id: str
    action: str
    state: str
    execute: bool
    duplicate: bool
    first_reserved_at: str
    verification: VerifiedServerCommandSignature
    reason: str


class AuraSecNativeExecutionGuard:
    """Durable native-side exactly-once admission for bounded Aura Sec commands.

    The guard deliberately does not execute shell commands, scripts, binaries or OS actions.
    A native endpoint must first verify and reserve a server-signed ``SignedSecurityCommand``
    here, then dispatch the already-bounded action through a separate platform executor.

    The reservation is persisted *before* a side effect begins. If the endpoint crashes after
    reservation, the same command is never automatically executed again because the system
    cannot safely know whether the side effect happened. Operators must reconcile that command
    and, if another attempt is appropriate, authorize a new command id.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_sec_native_execution_guard (
                    device_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    server_payload_digest TEXT NOT NULL,
                    signed_envelope_digest TEXT NOT NULL,
                    signer_key_id TEXT NOT NULL,
                    signer_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    completed_at TEXT,
                    failed_at TEXT,
                    result_evidence_digest TEXT,
                    failure_code TEXT,
                    PRIMARY KEY(device_id, command_id)
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_native_execution_state
                ON aura_sec_native_execution_guard(device_id, state, reserved_at);
                """
            )

    @staticmethod
    def _validated_digest(value: str, *, field: str) -> str:
        digest = (value or "").strip().lower()
        if not _HEX_256.fullmatch(digest):
            raise ValueError(f"Aura Sec {field} must be lowercase SHA-256 hex")
        return digest

    def reserve_verified_command(
        self,
        command: SignedSecurityCommand,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        now: datetime | None = None,
    ) -> NativeExecutionReservation:
        """Verify every delivery, then atomically reserve the command id once.

        A duplicate is acknowledged only when the complete signed envelope is identical to the
        first accepted envelope. A command id reused with any changed authenticated field fails
        closed even if the new envelope has a valid trusted signature.
        """
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)

        verification = verify_signed_security_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        server_payload_digest = self._validated_digest(
            verification.evidence_digest,
            field="verified server-command payload digest",
        )
        envelope_digest = _signed_envelope_digest(command)

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT action_type,server_payload_digest,signed_envelope_digest,state,reserved_at,
                          signer_key_id,signer_fingerprint
                   FROM aura_sec_native_execution_guard
                   WHERE device_id=? AND command_id=?""",
                (command.device_id, command.command_id),
            ).fetchone()

            if row is not None:
                stored_payload = str(row["server_payload_digest"] or "").strip().lower()
                stored_envelope = str(row["signed_envelope_digest"] or "").strip().lower()
                stored_key = str(row["signer_key_id"] or "")
                stored_fingerprint = str(row["signer_fingerprint"] or "").strip().lower()
                if not (
                    secrets.compare_digest(stored_payload, server_payload_digest)
                    and secrets.compare_digest(stored_envelope, envelope_digest)
                    and secrets.compare_digest(stored_key, verification.signer_key_id)
                    and secrets.compare_digest(
                        stored_fingerprint,
                        verification.public_key_fingerprint,
                    )
                    and secrets.compare_digest(str(row["action_type"]), command.action.value)
                ):
                    raise PermissionError(
                        "Aura Sec native command id was rebound to a different signed command"
                    )

                state = str(row["state"])
                if state not in {"reserved", *_FINAL_STATES}:
                    raise PermissionError("Aura Sec native execution state is invalid")
                return NativeExecutionReservation(
                    command_id=command.command_id,
                    device_id=command.device_id,
                    action=command.action.value,
                    state=state,
                    execute=False,
                    duplicate=True,
                    first_reserved_at=str(row["reserved_at"]),
                    verification=verification,
                    reason=(
                        "This exact signed command was already reserved. Native execution is suppressed "
                        "to prevent a duplicate side effect."
                    ),
                )

            reserved_at = _iso(current)
            con.execute(
                """INSERT INTO aura_sec_native_execution_guard
                   (device_id,command_id,action_type,server_payload_digest,signed_envelope_digest,
                    signer_key_id,signer_fingerprint,state,reserved_at)
                   VALUES (?,?,?,?,?,?,?,'reserved',?)""",
                (
                    command.device_id,
                    command.command_id,
                    command.action.value,
                    server_payload_digest,
                    envelope_digest,
                    verification.signer_key_id,
                    verification.public_key_fingerprint,
                    reserved_at,
                ),
            )

        return NativeExecutionReservation(
            command_id=command.command_id,
            device_id=command.device_id,
            action=command.action.value,
            state="reserved",
            execute=True,
            duplicate=False,
            first_reserved_at=reserved_at,
            verification=verification,
            reason=(
                "The trusted signed command was durably reserved before native execution. "
                "The platform executor may perform this bounded action once."
            ),
        )

    def mark_completed(
        self,
        *,
        device_id: str,
        command_id: str,
        result_evidence_digest: str,
        now: datetime | None = None,
    ) -> dict:
        digest = self._validated_digest(
            result_evidence_digest,
            field="native execution result evidence digest",
        )
        completed_at = _iso(now)
        already_complete = False
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT state,result_evidence_digest,completed_at
                   FROM aura_sec_native_execution_guard
                   WHERE device_id=? AND command_id=?""",
                (device_id, command_id),
            ).fetchone()
            if row is None:
                raise ValueError("Aura Sec native command was not reserved for execution")
            state = str(row["state"])
            if state == "completed":
                stored = str(row["result_evidence_digest"] or "").strip().lower()
                if not secrets.compare_digest(stored, digest):
                    raise ValueError("Conflicting Aura Sec completion evidence for command")
                already_complete = True
            elif state != "reserved":
                raise ValueError("Only a reserved Aura Sec native command can complete")
            else:
                updated = con.execute(
                    """UPDATE aura_sec_native_execution_guard
                       SET state='completed',completed_at=?,result_evidence_digest=?
                       WHERE device_id=? AND command_id=? AND state='reserved'""",
                    (completed_at, digest, device_id, command_id),
                )
                if updated.rowcount != 1:
                    raise PermissionError("Aura Sec native execution state changed concurrently")
        result = self.get(device_id=device_id, command_id=command_id)
        if already_complete and result.get("state") != "completed":
            raise PermissionError("Aura Sec native completion state changed concurrently")
        return result

    def mark_failed(
        self,
        *,
        device_id: str,
        command_id: str,
        failure_code: str,
        now: datetime | None = None,
    ) -> dict:
        code = (failure_code or "").strip()
        if not _FAILURE_CODE.fullmatch(code):
            raise ValueError("Aura Sec native execution failure code is invalid")
        failed_at = _iso(now)
        already_failed = False
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT state,failure_code FROM aura_sec_native_execution_guard
                   WHERE device_id=? AND command_id=?""",
                (device_id, command_id),
            ).fetchone()
            if row is None:
                raise ValueError("Aura Sec native command was not reserved for execution")
            state = str(row["state"])
            if state == "failed":
                if not secrets.compare_digest(str(row["failure_code"] or ""), code):
                    raise ValueError("Conflicting Aura Sec failure evidence for command")
                already_failed = True
            elif state != "reserved":
                raise ValueError("Only a reserved Aura Sec native command can fail")
            else:
                updated = con.execute(
                    """UPDATE aura_sec_native_execution_guard
                       SET state='failed',failed_at=?,failure_code=?
                       WHERE device_id=? AND command_id=? AND state='reserved'""",
                    (failed_at, code, device_id, command_id),
                )
                if updated.rowcount != 1:
                    raise PermissionError("Aura Sec native execution state changed concurrently")
        result = self.get(device_id=device_id, command_id=command_id)
        if already_failed and result.get("state") != "failed":
            raise PermissionError("Aura Sec native failure state changed concurrently")
        return result

    def get(self, *, device_id: str, command_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT device_id,command_id,action_type,state,reserved_at,completed_at,failed_at,
                          result_evidence_digest,failure_code,server_payload_digest,
                          signed_envelope_digest,signer_key_id,signer_fingerprint
                   FROM aura_sec_native_execution_guard
                   WHERE device_id=? AND command_id=?""",
                (device_id, command_id),
            ).fetchone()
        if row is None:
            raise ValueError("Aura Sec native execution record not found")
        return dict(row)


__all__ = [
    "AuraSecNativeExecutionGuard",
    "NativeExecutionReservation",
]
