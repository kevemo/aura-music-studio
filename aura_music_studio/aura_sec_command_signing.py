from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ConfigDict, Field, field_validator

from .aura_sec_protocol import SecurityCommand

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")
_DOMAIN = "AURA-SEC-SERVER-COMMAND-V1\n"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    return hashlib.sha256(_public_key_bytes(public_key)).hexdigest()


def canonical_server_command_payload(
    command: SecurityCommand,
    *,
    signer_key_id: str,
    public_key_fingerprint_hex: str,
    signing_schema_version: int = 1,
) -> bytes:
    key_id = (signer_key_id or "").strip()
    fingerprint = (public_key_fingerprint_hex or "").strip().lower()
    if signing_schema_version != 1:
        raise ValueError("Unsupported Aura Sec server-command signing schema")
    if not _KEY_ID.fullmatch(key_id):
        raise ValueError("Invalid Aura Sec server-command signer key id")
    if not _HEX_256.fullmatch(fingerprint):
        raise ValueError("Invalid Aura Sec server-command public-key fingerprint")

    payload = {
        "command": command.model_dump(mode="json"),
        "key_algorithm": "ed25519",
        "public_key_fingerprint": fingerprint,
        "signer_key_id": key_id,
        "signing_schema_version": signing_schema_version,
    }
    return (
        _DOMAIN
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


class SignedSecurityCommand(SecurityCommand):
    """A bounded Aura Sec command authenticated by a pinned server signing key.

    The command fields stay top-level so native agents consume the same bounded command
    schema, while the additional metadata makes every field cryptographically tamper-evident.
    The private signing key is never serialized into this object.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    signing_schema_version: int = Field(default=1, ge=1, le=1)
    signer_key_id: str = Field(min_length=3, max_length=80)
    key_algorithm: str = Field(default="ed25519", min_length=1, max_length=32)
    public_key_fingerprint: str = Field(min_length=64, max_length=64)
    payload_digest: str = Field(min_length=64, max_length=64)
    signature_b64: str = Field(min_length=80, max_length=128)

    @field_validator("signer_key_id")
    @classmethod
    def valid_key_id(cls, value: str) -> str:
        if not _KEY_ID.fullmatch(value):
            raise ValueError("Invalid Aura Sec server-command signer key id")
        return value

    @field_validator("key_algorithm")
    @classmethod
    def ed25519_only(cls, value: str) -> str:
        value = value.lower()
        if value != "ed25519":
            raise ValueError("Aura Sec server commands currently require Ed25519")
        return value

    @field_validator("public_key_fingerprint", "payload_digest")
    @classmethod
    def canonical_digest(cls, value: str) -> str:
        value = value.lower()
        if not _HEX_256.fullmatch(value):
            raise ValueError("Aura Sec command digest/fingerprint must be lowercase SHA-256 hex")
        return value

    def unsigned_command(self) -> SecurityCommand:
        fields = SecurityCommand.model_fields
        return SecurityCommand.model_validate({name: getattr(self, name) for name in fields})

    def canonical_signed_payload(self) -> bytes:
        return canonical_server_command_payload(
            self.unsigned_command(),
            signer_key_id=self.signer_key_id,
            public_key_fingerprint_hex=self.public_key_fingerprint,
            signing_schema_version=self.signing_schema_version,
        )


@dataclass(frozen=True)
class VerifiedServerCommandSignature:
    signer_key_id: str
    key_algorithm: str
    public_key_fingerprint: str
    evidence_digest: str


class ServerCommandSigner(Protocol):
    def sign_command(self, command: SecurityCommand) -> SignedSecurityCommand: ...


class SelfHostedEd25519CommandSigner:
    """Concrete self-hostable server signer.

    Deployment code supplies the private key from a protected secret store, encrypted file,
    HSM adapter or similar custody layer. This class deliberately never generates or writes a
    long-lived production key implicitly, preventing accidental key rotation on process restart.
    """

    def __init__(self, private_key: Ed25519PrivateKey, *, key_id: str):
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("Aura Sec command signer requires an Ed25519 private key")
        key_id = (key_id or "").strip()
        if not _KEY_ID.fullmatch(key_id):
            raise ValueError("Invalid Aura Sec server-command signer key id")
        self._private_key = private_key
        self.key_id = key_id
        self.public_key = private_key.public_key()
        self.public_key_fingerprint = public_key_fingerprint(self.public_key)

    @classmethod
    def from_private_key_pem(
        cls,
        pem: bytes,
        *,
        key_id: str,
        password: bytes | None = None,
    ) -> "SelfHostedEd25519CommandSigner":
        try:
            loaded = serialization.load_pem_private_key(pem, password=password)
        except Exception as exc:
            raise ValueError("Aura Sec server-command private key could not be loaded") from exc
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError("Aura Sec server-command private key must be Ed25519")
        return cls(loaded, key_id=key_id)

    def public_key_raw(self) -> bytes:
        return _public_key_bytes(self.public_key)

    def sign_command(self, command: SecurityCommand) -> SignedSecurityCommand:
        if not isinstance(command, SecurityCommand):
            command = SecurityCommand.model_validate(command)
        payload = canonical_server_command_payload(
            command,
            signer_key_id=self.key_id,
            public_key_fingerprint_hex=self.public_key_fingerprint,
        )
        digest = hashlib.sha256(payload).hexdigest()
        signature = self._private_key.sign(payload)
        return SignedSecurityCommand(
            **command.model_dump(),
            signing_schema_version=1,
            signer_key_id=self.key_id,
            key_algorithm="ed25519",
            public_key_fingerprint=self.public_key_fingerprint,
            payload_digest=digest,
            signature_b64=base64.b64encode(signature).decode("ascii"),
        )


def _trusted_public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError("Trusted Aura Sec server key must be raw Ed25519 bytes or Ed25519PublicKey")
    raw = bytes(value)
    if len(raw) != 32:
        raise ValueError("Trusted Aura Sec Ed25519 public key must be 32 raw bytes")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise ValueError("Trusted Aura Sec Ed25519 public key is invalid") from exc


def verify_signed_security_command(
    signed: SignedSecurityCommand,
    *,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
    expected_device_id: str | None = None,
    now: datetime | None = None,
    future_skew_seconds: int = 120,
) -> VerifiedServerCommandSignature:
    """Verify a server command before any native executor is allowed to act on it."""
    if not isinstance(signed, SignedSecurityCommand):
        signed = SignedSecurityCommand.model_validate(signed)
    if not 0 <= int(future_skew_seconds) <= 300:
        raise ValueError("Aura Sec command clock-skew allowance is invalid")
    if expected_device_id is not None and not secrets.compare_digest(
        signed.device_id, str(expected_device_id)
    ):
        raise PermissionError("Aura Sec signed command targets a different device")

    trusted_value = trusted_public_keys.get(signed.signer_key_id)
    if trusted_value is None:
        raise PermissionError("Aura Sec server-command signer is not trusted")
    public_key = _trusted_public_key(trusted_value)
    fingerprint = public_key_fingerprint(public_key)
    if not secrets.compare_digest(fingerprint, signed.public_key_fingerprint):
        raise PermissionError("Aura Sec server-command signer fingerprint does not match pinned trust")

    payload = signed.canonical_signed_payload()
    digest = hashlib.sha256(payload).hexdigest()
    if not secrets.compare_digest(digest, signed.payload_digest):
        raise PermissionError("Aura Sec signed-command digest does not match the canonical payload")
    try:
        signature = base64.b64decode(signed.signature_b64, validate=True)
    except Exception as exc:
        raise PermissionError("Aura Sec server-command signature is not valid base64") from exc
    if len(signature) != 64:
        raise PermissionError("Aura Sec Ed25519 server-command signature length is invalid")
    try:
        public_key.verify(signature, payload)
    except InvalidSignature as exc:
        raise PermissionError("Aura Sec server-command signature verification failed") from exc
    except Exception as exc:
        raise PermissionError("Aura Sec server-command verification failed closed") from exc

    current = _utc(now or datetime.now(timezone.utc))
    issued = _utc(signed.issued_at)
    expires = _utc(signed.expires_at)
    if issued > current + timedelta(seconds=int(future_skew_seconds)):
        raise PermissionError("Aura Sec signed command issuance is too far in the future")
    if current >= expires:
        raise PermissionError("Aura Sec signed command has expired")

    return VerifiedServerCommandSignature(
        signer_key_id=signed.signer_key_id,
        key_algorithm="ed25519",
        public_key_fingerprint=fingerprint,
        evidence_digest=digest,
    )


__all__ = [
    "SelfHostedEd25519CommandSigner",
    "ServerCommandSigner",
    "SignedSecurityCommand",
    "VerifiedServerCommandSignature",
    "canonical_server_command_payload",
    "public_key_fingerprint",
    "verify_signed_security_command",
]
