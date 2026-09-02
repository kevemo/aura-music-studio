from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .aura_sec_command_signing import SignedSecurityCommand, verify_signed_security_command
from .aura_sec_device_attestation import (
    AttestedNativePlatformDispatchResult,
    AuraSecAttestedNativePlatformExecutor,
    DeviceAttestationAssertion,
    DeviceAttestationChallenge,
    SelfHostedEd25519DeviceAttestor,
)

_HEX_256 = frozenset("0123456789abcdef")
_BINDING_DOMAIN = "AURA-SEC-DEVICE-COMMAND-BINDING-V1\n"


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec command-bound attestation timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _sha256_hex(value: str, *, label: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in _HEX_256 for char in digest):
        raise ValueError(f"Aura Sec {label} must be lowercase SHA-256 hex")
    return digest


class CommandBoundDeviceAttestationChallenge(BaseModel):
    """One-time device challenge bound to one exact signed server-command payload.

    `command_payload_digest` is the SHA-256 digest already carried by a verified
    `SignedSecurityCommand`. The nested challenge remains authoritative for one-time replay
    prevention and device/platform/executor/key identity. This layer adds exact signed-payload
    binding without creating a second challenge database or execution path.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    command_payload_digest: str = Field(min_length=64, max_length=64)
    challenge: DeviceAttestationChallenge

    @field_validator("command_payload_digest")
    @classmethod
    def validate_command_payload_digest(cls, value: str) -> str:
        return _sha256_hex(value, label="signed-command payload digest")


class CommandBoundDeviceAttestationAssertion(BaseModel):
    """Device-key proof that binds one base assertion to one signed command digest."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    attestation_kind: str = Field(
        default="device-key-possession+command-binding",
        pattern=r"^device-key-possession\+command-binding$",
    )
    command_payload_digest: str = Field(min_length=64, max_length=64)
    device_assertion: DeviceAttestationAssertion
    binding_payload_digest: str = Field(min_length=64, max_length=64)
    binding_signature_b64: str = Field(min_length=80, max_length=128)

    @field_validator("command_payload_digest", "binding_payload_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _sha256_hex(value, label="command-bound attestation digest")

    def canonical_binding_payload(self) -> bytes:
        base = self.device_assertion
        payload = {
            "attestation_kind": self.attestation_kind,
            "base_assertion_payload_digest": base.payload_digest,
            "challenge_id": base.challenge_id,
            "command_id": base.command_id,
            "command_payload_digest": self.command_payload_digest,
            "device_id": base.device_id,
            "device_key_id": base.device_key_id,
            "executor_id": base.executor_id,
            "platform": base.platform,
            "public_key_fingerprint": base.public_key_fingerprint,
            "schema_version": self.schema_version,
        }
        return (
            _BINDING_DOMAIN
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")


class SelfHostedEd25519CommandBoundDeviceAttestor:
    """Create the existing device assertion plus an exact signed-command binding proof.

    Deployment code supplies the same protected Ed25519 device key used by the existing Aura Sec
    device attestor. No key is generated, persisted, exported or rotated implicitly here.
    """

    def __init__(self, private_key: Ed25519PrivateKey, *, key_id: str):
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("Aura Sec command-bound device attestor requires an Ed25519 private key")
        self._private_key = private_key
        self.base_attestor = SelfHostedEd25519DeviceAttestor(private_key, key_id=key_id)
        self.key_id = self.base_attestor.key_id
        self.public_key = self.base_attestor.public_key
        self.public_key_fingerprint = self.base_attestor.public_key_fingerprint

    def public_key_raw(self) -> bytes:
        return self.base_attestor.public_key_raw()

    def sign_challenge(
        self,
        challenge: CommandBoundDeviceAttestationChallenge,
        *,
        attested_at: datetime | None = None,
    ) -> CommandBoundDeviceAttestationAssertion:
        if not isinstance(challenge, CommandBoundDeviceAttestationChallenge):
            challenge = CommandBoundDeviceAttestationChallenge.model_validate(challenge)
        base_assertion = self.base_attestor.sign_challenge(
            challenge.challenge,
            attested_at=_utc(attested_at),
        )
        unsigned = CommandBoundDeviceAttestationAssertion(
            command_payload_digest=challenge.command_payload_digest,
            device_assertion=base_assertion,
            binding_payload_digest="0" * 64,
            binding_signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
        )
        payload = unsigned.canonical_binding_payload()
        digest = hashlib.sha256(payload).hexdigest()
        signature = self._private_key.sign(payload)
        return unsigned.model_copy(
            update={
                "binding_payload_digest": digest,
                "binding_signature_b64": base64.b64encode(signature).decode("ascii"),
            }
        )


class AuraSecCommandBoundAttestedNativeExecutor:
    """Require exact signed-command binding before the existing attested native executor.

    This is a strict wrapper around `AuraSecAttestedNativePlatformExecutor`. It does not own a
    second device registry, challenge store, sequence counter or native action path. Verification
    order is:

      1. verify the server-signed command;
      2. require the command digest in the device proof to equal that exact signed payload;
      3. independently re-hash the nested base device assertion;
      4. verify the additional device-key binding signature using the already-enrolled public key;
      5. delegate to the existing attested executor, which atomically consumes the one-time
         challenge and then re-applies signed-command, anti-rollback and execute-once admission.

    A malformed binding proof never consumes the underlying one-time challenge and never reaches
    the native platform adapter.
    """

    def __init__(self, attested_executor: AuraSecAttestedNativePlatformExecutor):
        if not isinstance(attested_executor, AuraSecAttestedNativePlatformExecutor):
            raise TypeError(
                "Aura Sec command-bound execution requires AuraSecAttestedNativePlatformExecutor"
            )
        self.attested_executor = attested_executor
        self.attestation_store = attested_executor.attestation_store

    def issue_challenge(
        self,
        command: SignedSecurityCommand,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        platform: str,
        device_key_id: str,
        ttl_seconds: int = 90,
        now: datetime | None = None,
    ) -> CommandBoundDeviceAttestationChallenge:
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)
        verify_signed_security_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        challenge = self.attested_executor.issue_challenge(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            platform=platform,
            device_key_id=device_key_id,
            ttl_seconds=ttl_seconds,
            now=current,
        )
        return CommandBoundDeviceAttestationChallenge(
            command_payload_digest=command.payload_digest,
            challenge=challenge,
        )

    def _active_enrolled_public_key(
        self,
        assertion: DeviceAttestationAssertion,
    ) -> Ed25519PublicKey:
        with sqlite3.connect(self.attestation_store.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT public_key, public_key_fingerprint, state, platform, executor_id
                FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (assertion.device_id, assertion.device_key_id),
            ).fetchone()
        if row is None or row["state"] != "active":
            raise PermissionError("Aura Sec command binding requires an actively enrolled device key")
        if str(row["platform"]) != assertion.platform:
            raise PermissionError("Aura Sec command binding device platform does not match enrollment")
        if not secrets.compare_digest(str(row["executor_id"]), assertion.executor_id):
            raise PermissionError("Aura Sec command binding executor does not match enrollment")
        if not secrets.compare_digest(
            str(row["public_key_fingerprint"]), assertion.public_key_fingerprint
        ):
            raise PermissionError("Aura Sec command binding key fingerprint does not match enrollment")
        raw = bytes(row["public_key"])
        if len(raw) != 32:
            raise PermissionError("Aura Sec enrolled device public key length is invalid")
        try:
            return Ed25519PublicKey.from_public_bytes(raw)
        except Exception as exc:
            raise PermissionError("Aura Sec enrolled device public key is invalid") from exc

    @staticmethod
    def _verify_base_assertion_digest(assertion: DeviceAttestationAssertion) -> None:
        payload = assertion.canonical_payload()
        digest = hashlib.sha256(payload).hexdigest()
        if not secrets.compare_digest(digest, assertion.payload_digest):
            raise PermissionError("Aura Sec base device-attestation payload digest does not match")

    def _verify_binding_signature(
        self,
        assertion: CommandBoundDeviceAttestationAssertion,
    ) -> None:
        payload = assertion.canonical_binding_payload()
        digest = hashlib.sha256(payload).hexdigest()
        if not secrets.compare_digest(digest, assertion.binding_payload_digest):
            raise PermissionError("Aura Sec command-bound attestation digest does not match")
        try:
            signature = base64.b64decode(assertion.binding_signature_b64, validate=True)
        except Exception as exc:
            raise PermissionError("Aura Sec command-binding signature is not valid base64") from exc
        if len(signature) != 64:
            raise PermissionError("Aura Sec command-binding signature length is invalid")
        public_key = self._active_enrolled_public_key(assertion.device_assertion)
        try:
            public_key.verify(signature, payload)
        except InvalidSignature as exc:
            raise PermissionError("Aura Sec command-binding signature verification failed") from exc
        except Exception as exc:
            raise PermissionError("Aura Sec command-binding verification failed closed") from exc

    def dispatch(
        self,
        command: SignedSecurityCommand,
        assertion: CommandBoundDeviceAttestationAssertion,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        platform: str,
        now: datetime | None = None,
    ) -> AttestedNativePlatformDispatchResult:
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)
        if not isinstance(assertion, CommandBoundDeviceAttestationAssertion):
            assertion = CommandBoundDeviceAttestationAssertion.model_validate(assertion)
        verify_signed_security_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        if not secrets.compare_digest(assertion.command_payload_digest, command.payload_digest):
            raise PermissionError(
                "Aura Sec device attestation is bound to a different signed-command payload"
            )
        base = assertion.device_assertion
        if not secrets.compare_digest(base.device_id, command.device_id):
            raise PermissionError("Aura Sec command-bound assertion targets a different device")
        if not secrets.compare_digest(base.command_id, command.command_id):
            raise PermissionError("Aura Sec command-bound assertion targets a different command")
        self._verify_base_assertion_digest(base)
        self._verify_binding_signature(assertion)
        return self.attested_executor.dispatch(
            command,
            base,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            platform=platform,
            now=current,
        )


__all__ = [
    "AuraSecCommandBoundAttestedNativeExecutor",
    "CommandBoundDeviceAttestationAssertion",
    "CommandBoundDeviceAttestationChallenge",
    "SelfHostedEd25519CommandBoundDeviceAttestor",
]
