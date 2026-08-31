from __future__ import annotations

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
from .aura_sec_native_execution_guard import (
    AuraSecNativeExecutionGuard,
    NativeExecutionReservation,
)

_MAX_SEQUENCE = 9_223_372_036_854_775_807
_SEQUENCE_NONCE = re.compile(
    r"^aseq1\.(?P<sequence>[1-9][0-9]{0,18})\.(?P<entropy>[A-Za-z0-9_-]{32,128})$"
)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec command-sequence timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def sequenced_command_nonce(sequence: int, *, entropy: str | None = None) -> str:
    """Create a signed command nonce that carries the authenticated device poll sequence.

    The sequence comes from the verifier-backed monotonic NativeCommandPoll. The random suffix
    preserves nonce uniqueness while the structured prefix gives the endpoint an authenticated
    anti-rollback value because the complete nonce is covered by the server Ed25519 signature.
    """
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError("Aura Sec command sequence must be an integer")
    if not 1 <= sequence <= _MAX_SEQUENCE:
        raise ValueError("Aura Sec command sequence is outside the supported range")
    token = entropy if entropy is not None else secrets.token_urlsafe(32)
    token = str(token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Aura Sec command sequence entropy is invalid")
    return f"aseq1.{sequence}.{token}"


def command_sequence_from_nonce(nonce: str) -> int:
    match = _SEQUENCE_NONCE.fullmatch(str(nonce or "").strip())
    if not match:
        raise PermissionError("Aura Sec signed command is missing a valid anti-rollback sequence")
    sequence = int(match.group("sequence"))
    if not 1 <= sequence <= _MAX_SEQUENCE:
        raise PermissionError("Aura Sec signed command sequence is outside the supported range")
    return sequence


@dataclass(frozen=True)
class SequencedCommandAcceptance:
    command_id: str
    device_id: str
    sequence: int
    duplicate: bool
    accepted_at: str
    verification: VerifiedServerCommandSignature


@dataclass(frozen=True)
class SequencedExecutionReservation:
    sequence: SequencedCommandAcceptance
    execution: NativeExecutionReservation


class AuraSecNativeCommandSequenceGuard:
    """Durable per-device high-water mark for authenticated server command sequences.

    Sequence admission happens only after the existing Ed25519 server-command verification.
    The endpoint accepts a strictly newer sequence, or an exact retransmission of the command
    already recorded at the current sequence. Any older sequence, or reuse of the current
    sequence for different signed content, fails closed.
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
                CREATE TABLE IF NOT EXISTS aura_sec_native_command_sequence_state (
                    device_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL,
                    last_command_id TEXT NOT NULL,
                    last_payload_digest TEXT NOT NULL,
                    accepted_at TEXT NOT NULL
                );
                """
            )

    def accept_verified_command(
        self,
        command: SignedSecurityCommand,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        now: datetime | None = None,
    ) -> SequencedCommandAcceptance:
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)

        verification = verify_signed_security_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        sequence = command_sequence_from_nonce(command.nonce)
        payload_digest = verification.evidence_digest
        accepted_at = current.isoformat()
        duplicate = False

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT last_sequence,last_command_id,last_payload_digest,accepted_at
                   FROM aura_sec_native_command_sequence_state WHERE device_id=?""",
                (command.device_id,),
            ).fetchone()

            if row is None:
                con.execute(
                    """INSERT INTO aura_sec_native_command_sequence_state
                       (device_id,last_sequence,last_command_id,last_payload_digest,accepted_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        command.device_id,
                        sequence,
                        command.command_id,
                        payload_digest,
                        accepted_at,
                    ),
                )
            else:
                last_sequence = int(row["last_sequence"])
                if sequence < last_sequence:
                    raise PermissionError(
                        "Aura Sec signed command sequence moved backwards and was rejected"
                    )
                if sequence == last_sequence:
                    same_command = secrets.compare_digest(
                        str(row["last_command_id"]), command.command_id
                    )
                    same_payload = secrets.compare_digest(
                        str(row["last_payload_digest"]).lower(), payload_digest.lower()
                    )
                    if not (same_command and same_payload):
                        raise PermissionError(
                            "Aura Sec signed command sequence was reused for different content"
                        )
                    duplicate = True
                    accepted_at = str(row["accepted_at"])
                else:
                    updated = con.execute(
                        """UPDATE aura_sec_native_command_sequence_state
                           SET last_sequence=?,last_command_id=?,last_payload_digest=?,accepted_at=?
                           WHERE device_id=? AND last_sequence<?""",
                        (
                            sequence,
                            command.command_id,
                            payload_digest,
                            accepted_at,
                            command.device_id,
                            sequence,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise PermissionError(
                            "Aura Sec signed command sequence state changed concurrently"
                        )

        return SequencedCommandAcceptance(
            command_id=command.command_id,
            device_id=command.device_id,
            sequence=sequence,
            duplicate=duplicate,
            accepted_at=accepted_at,
            verification=verification,
        )

    def state(self, device_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT device_id,last_sequence,last_command_id,last_payload_digest,accepted_at
                   FROM aura_sec_native_command_sequence_state WHERE device_id=?""",
                (device_id,),
            ).fetchone()
        return dict(row) if row is not None else None


class AuraSecSequencedNativeExecutionGate:
    """Required native admission path combining anti-rollback with execute-once protection."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        sequences: AuraSecNativeCommandSequenceGuard | None = None,
        executions: AuraSecNativeExecutionGuard | None = None,
    ):
        self.sequences = sequences or AuraSecNativeCommandSequenceGuard(db_path)
        self.executions = executions or AuraSecNativeExecutionGuard(db_path)

    def reserve_verified_command(
        self,
        command: SignedSecurityCommand,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        now: datetime | None = None,
    ) -> SequencedExecutionReservation:
        sequence = self.sequences.accept_verified_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=now,
        )
        execution = self.executions.reserve_verified_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=now,
        )
        return SequencedExecutionReservation(sequence=sequence, execution=execution)


__all__ = [
    "AuraSecNativeCommandSequenceGuard",
    "AuraSecSequencedNativeExecutionGate",
    "SequencedCommandAcceptance",
    "SequencedExecutionReservation",
    "command_sequence_from_nonce",
    "sequenced_command_nonce",
]
