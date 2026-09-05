from __future__ import annotations

import base64
import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .accounts import AccountStore
from .aura_sec_action_parameters import validated_command_parameters
from .aura_sec_command_delivery import AuraSecCommandDeliveryStore
from .aura_sec_command_sequence import sequenced_command_nonce
from .aura_sec_command_signing import ServerCommandSigner, SignedSecurityCommand
from .aura_sec_command_store import AuraSecCommandStore
from .aura_sec_store import AuraSecStore


_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_KEY_ALGORITHMS = {"ed25519", "p256", "rsa-pss-sha256"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _hash_nonce(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class NativeCommandPoll(BaseModel):
    """Short-lived request whose canonical payload must be signed by the enrolled device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    issued_at: datetime
    expires_at: datetime
    agent_version: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=80)
    session_nonce: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9._~-]+$")

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validity_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("native poll expiry must be after issuance")
        if (self.expires_at - self.issued_at).total_seconds() > 120:
            raise ValueError("native poll validity window cannot exceed two minutes")
        return self

    def signed_payload(self) -> bytes:
        parts = (
            str(self.schema_version),
            self.device_id,
            str(self.sequence),
            self.issued_at.astimezone(timezone.utc).isoformat(),
            self.expires_at.astimezone(timezone.utc).isoformat(),
            self.agent_version,
            self.policy_version,
            self.session_nonce,
        )
        if any("\n" in part or "\r" in part for part in parts):
            raise ValueError("Native poll canonical fields must not contain newlines")
        return ("AURA-SEC-NATIVE-POLL-V1\n" + "\n".join(parts) + "\n").encode("utf-8")


@dataclass(frozen=True)
class VerifiedNativePollSignature:
    """Evidence returned only by a trusted native-device signature verifier."""

    public_key_fingerprint: str
    verifier_id: str
    key_algorithm: str
    evidence_digest: str


NativePollSignatureVerifier = Callable[[str, bytes, bytes], VerifiedNativePollSignature | None]


class AuraSecNativeBridge:
    """Trusted gateway between signed native sessions and bounded, server-signed commands.

    This class is not mounted as a member/browser API. It has no boolean signature bypass.
    A trusted verifier adapter must validate the canonical poll payload against the enrolled
    device identity. Replay state advances only after that proof succeeds. The bridge issues
    at most one previously-approved action per verified poll, applies the strict parameter
    firewall, durably persists the exact signed envelope before transport and redelivers that
    same envelope after response loss. It never falls back to unsigned native commands.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        security: AuraSecStore | None = None,
        commands: AuraSecCommandStore | None = None,
        command_signer: ServerCommandSigner | None = None,
        deliveries: AuraSecCommandDeliveryStore | None = None,
    ):
        self.accounts = accounts or AccountStore()
        self.security = security or AuraSecStore(self.accounts)
        self.commands = commands or AuraSecCommandStore(self.accounts, self.security)
        self.command_signer = command_signer
        self.deliveries = deliveries or AuraSecCommandDeliveryStore(self.accounts)
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
                CREATE TABLE IF NOT EXISTS aura_sec_native_poll_state (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    last_nonce_hash TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(device_id) REFERENCES aura_sec_devices(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_sec_native_poll_user
                ON aura_sec_native_poll_state(user_id,last_verified_at);
                """
            )

    def _device_key_fingerprint(self, user_id: str, device_id: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT public_key_fingerprint FROM aura_sec_devices WHERE user_id=? AND id=?",
                (user_id, device_id),
            ).fetchone()
        if not row or not row["public_key_fingerprint"]:
            raise PermissionError("Aura Sec enrolled device key identity is unavailable")
        fingerprint = str(row["public_key_fingerprint"]).strip().lower()
        if not _HEX_256.fullmatch(fingerprint):
            raise PermissionError("Aura Sec enrolled device key fingerprint is invalid")
        return fingerprint

    def _validate_poll_context(
        self,
        user_id: str,
        poll: NativeCommandPoll,
        *,
        now: datetime | None = None,
    ) -> datetime:
        current = (now or _now()).astimezone(timezone.utc)
        if current < poll.issued_at or current >= poll.expires_at:
            raise PermissionError("Aura Sec native poll is outside its validity window")
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required")
        device = self.security.get_device(user_id, poll.device_id)
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot poll for commands")
        return current

    def _verify_poll_signature(
        self,
        user_id: str,
        poll: NativeCommandPoll,
        *,
        signature_b64: str,
        signature_verifier: NativePollSignatureVerifier | None,
    ) -> VerifiedNativePollSignature:
        if signature_verifier is None:
            raise PermissionError("A trusted Aura Sec native poll signature verifier is required")
        try:
            signature = base64.b64decode((signature_b64 or "").strip(), validate=True)
        except Exception as exc:
            raise PermissionError("Aura Sec native poll signature is not valid base64") from exc
        if not 32 <= len(signature) <= 1024:
            raise PermissionError("Aura Sec native poll signature length is invalid")

        fingerprint = self._device_key_fingerprint(user_id, poll.device_id)
        payload = poll.signed_payload()
        try:
            verified = signature_verifier(fingerprint, payload, signature)
        except Exception as exc:
            raise PermissionError("Aura Sec native poll signature verification failed closed") from exc
        if not isinstance(verified, VerifiedNativePollSignature):
            raise PermissionError("Aura Sec native poll signature was not verified")

        proof_fingerprint = (verified.public_key_fingerprint or "").strip().lower()
        evidence_digest = (verified.evidence_digest or "").strip().lower()
        verifier_id = (verified.verifier_id or "").strip()
        key_algorithm = (verified.key_algorithm or "").strip().lower()
        if not secrets.compare_digest(proof_fingerprint, fingerprint):
            raise PermissionError("Verified native poll key does not match the enrolled device identity")
        expected_digest = hashlib.sha256(payload).hexdigest()
        if not _HEX_256.fullmatch(evidence_digest) or not secrets.compare_digest(evidence_digest, expected_digest):
            raise PermissionError("Verified native poll evidence digest does not match the signed payload")
        if not verifier_id or len(verifier_id) > 160:
            raise PermissionError("Trusted native poll verifier identity is required")
        if key_algorithm not in _ALLOWED_KEY_ALGORITHMS:
            raise PermissionError("Unsupported Aura Sec native poll key algorithm")

        return VerifiedNativePollSignature(
            public_key_fingerprint=proof_fingerprint,
            verifier_id=verifier_id,
            key_algorithm=key_algorithm,
            evidence_digest=evidence_digest,
        )

    def _accept_verified_poll_sequence(
        self,
        user_id: str,
        poll: NativeCommandPoll,
        *,
        verified_at: datetime,
    ) -> None:
        if self.security.licence(user_id).get("status") != "active":
            raise PermissionError("Active Aura Sec licence required")
        device = self.security.get_device(user_id, poll.device_id)
        if device.get("status") == "revoked" or device.get("revoked_at"):
            raise PermissionError("Revoked Aura Sec device cannot poll for commands")

        nonce_hash = _hash_nonce(poll.session_nonce)
        with self._connect() as con:
            row = con.execute(
                "SELECT last_sequence,last_nonce_hash FROM aura_sec_native_poll_state WHERE user_id=? AND device_id=?",
                (user_id, poll.device_id),
            ).fetchone()
            if row:
                if poll.sequence <= int(row["last_sequence"]):
                    raise PermissionError("Aura Sec native poll sequence was replayed or moved backwards")
                if secrets.compare_digest(nonce_hash, row["last_nonce_hash"]):
                    raise PermissionError("Aura Sec native poll nonce was replayed")
                updated = con.execute(
                    """UPDATE aura_sec_native_poll_state
                       SET last_sequence=?,last_nonce_hash=?,last_verified_at=?
                       WHERE user_id=? AND device_id=? AND last_sequence<?""",
                    (poll.sequence, nonce_hash, _iso(verified_at), user_id, poll.device_id, poll.sequence),
                )
                if updated.rowcount != 1:
                    raise PermissionError("Aura Sec native poll lost a sequence race")
            else:
                try:
                    con.execute(
                        """INSERT INTO aura_sec_native_poll_state
                           (device_id,user_id,last_sequence,last_nonce_hash,last_verified_at)
                           VALUES (?,?,?,?,?)""",
                        (poll.device_id, user_id, poll.sequence, nonce_hash, _iso(verified_at)),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PermissionError("Aura Sec native poll state changed concurrently") from exc

    def _next_approved_action_id(self, user_id: str, device_id: str) -> str | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT a.id
                   FROM aura_sec_actions a
                   LEFT JOIN aura_sec_commands c ON c.action_id=a.id
                   WHERE a.user_id=? AND a.device_id=? AND a.status='approved' AND c.id IS NULL
                   ORDER BY COALESCE(a.approved_at,a.requested_at) ASC
                   LIMIT 1""",
                (user_id, device_id),
            ).fetchone()
        return str(row["id"]) if row else None

    @staticmethod
    def _response(
        *,
        command: SignedSecurityCommand | None,
        poll: NativeCommandPoll,
        verification: VerifiedNativePollSignature,
        truth: str,
    ) -> dict:
        return {
            "command": command.model_dump(mode="json") if command is not None else None,
            "poll_sequence": poll.sequence,
            "verification": {
                "verifier_id": verification.verifier_id,
                "key_algorithm": verification.key_algorithm,
                "evidence_digest": verification.evidence_digest,
            },
            "member_browser_route_exposed": False,
            "truth": truth,
        }

    def poll_verified_command(
        self,
        user_id: str,
        poll: NativeCommandPoll,
        *,
        signature_b64: str,
        signature_verifier: NativePollSignatureVerifier | None,
        now: datetime | None = None,
    ) -> dict:
        """Return at most one valid durable server-signed command to a verified native client."""
        verified_at = self._validate_poll_context(user_id, poll, now=now)
        verification = self._verify_poll_signature(
            user_id,
            poll,
            signature_b64=signature_b64,
            signature_verifier=signature_verifier,
        )
        self._accept_verified_poll_sequence(user_id, poll, verified_at=verified_at)

        pending = self.deliveries.next_pending_for_device(
            user_id,
            poll.device_id,
            now=verified_at,
        )
        if pending is not None:
            return self._response(
                command=pending,
                poll=poll,
                verification=verification,
                truth=(
                    "A previously-issued signed command was durably redelivered unchanged after a new authenticated "
                    "device poll. The command id, nonce, parameters, expiry and server signature are identical."
                ),
            )

        action_id = self._next_approved_action_id(user_id, poll.device_id)
        if not action_id:
            return self._response(
                command=None,
                poll=poll,
                verification=verification,
                truth="No previously-approved bounded action is waiting for this verified device session.",
            )

        if self.command_signer is None:
            raise PermissionError("Aura Sec server command signer is required before native command issuance")

        action = self.security.get_action(user_id, action_id)
        parameters = validated_command_parameters(action["action_type"], action.get("details") or {})
        command = self.commands.issue_approved_action(
            user_id,
            action_id,
            policy_version=poll.policy_version,
            nonce=sequenced_command_nonce(poll.sequence),
            parameters=parameters,
            ttl_seconds=300,
        )
        try:
            signed_command = SignedSecurityCommand.model_validate(
                self.command_signer.sign_command(command)
            )
        except Exception as exc:
            self.deliveries.abandon_never_delivered(user_id, command.command_id)
            raise PermissionError("Aura Sec server command signing failed closed") from exc

        if signed_command.unsigned_command().model_dump(mode="json") != command.model_dump(mode="json"):
            self.deliveries.abandon_never_delivered(user_id, command.command_id)
            raise PermissionError("Aura Sec server command signer altered the bounded command payload")

        try:
            durable = self.deliveries.persist_first_delivery(
                user_id,
                signed_command,
                delivered_at=verified_at,
            )
        except Exception as exc:
            self.deliveries.abandon_never_delivered(user_id, command.command_id)
            raise PermissionError(
                "Aura Sec signed command could not be durably recorded before delivery"
            ) from exc

        return self._response(
            command=durable,
            poll=poll,
            verification=verification,
            truth=(
                "The command was issued only after verifier-backed enrolled-device signature proof, replay protection, "
                "prior action approval, strict per-action parameter validation, signed poll-sequence binding, "
                "Ed25519 server authentication and durable pre-transport persistence for exact retry delivery."
            ),
        )


__all__ = [
    "AuraSecNativeBridge",
    "NativeCommandPoll",
    "NativePollSignatureVerifier",
    "VerifiedNativePollSignature",
]
