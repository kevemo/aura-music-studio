from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .aura_sec_command_signing import (
    SignedSecurityCommand,
    public_key_fingerprint,
    verify_signed_security_command,
)
from .aura_sec_native_platform_execution import (
    AuraSecNativePlatformExecutor,
    NativePlatformDispatchResult,
)

_SUPPORTED_PLATFORMS = frozenset({"windows", "macos", "linux"})
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_COMMAND_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")
_EXECUTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_CHALLENGE_ID = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{32,160}$")
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ASSERTION_DOMAIN = "AURA-SEC-DEVICE-KEY-POSSESSION-V1\n"


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Aura Sec device-trust timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _parse_time(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def _platform(value: str) -> str:
    platform = str(value or "").strip().lower()
    if platform not in _SUPPORTED_PLATFORMS:
        raise PermissionError("Aura Sec device trust requires Windows, macOS or Linux")
    return platform


def _executor_id(value: str) -> str:
    executor = str(value or "").strip()
    if not _EXECUTOR_ID.fullmatch(executor):
        raise ValueError("Aura Sec native executor identity is invalid")
    return executor


def _key_id(value: str) -> str:
    key = str(value or "").strip()
    if not _KEY_ID.fullmatch(key):
        raise ValueError("Aura Sec device identity key id is invalid")
    return key


def _device_id(value: str) -> str:
    device = str(value or "").strip()
    if not _DEVICE_ID.fullmatch(device):
        raise ValueError("Aura Sec device identity is invalid")
    return device


def _command_id(value: str) -> str:
    command = str(value or "").strip()
    if not _COMMAND_ID.fullmatch(command):
        raise ValueError("Aura Sec command identity is invalid")
    return command


def _public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError("Aura Sec device identity key must be raw Ed25519 bytes or Ed25519PublicKey")
    raw = bytes(value)
    if len(raw) != 32:
        raise ValueError("Aura Sec device Ed25519 public key must be 32 raw bytes")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise ValueError("Aura Sec device Ed25519 public key is invalid") from exc


def _public_key_raw(value: bytes | Ed25519PublicKey) -> bytes:
    key = _public_key(value)
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


@dataclass(frozen=True)
class DeviceIdentityRegistration:
    device_id: str
    key_id: str
    platform: str
    executor_id: str
    public_key_fingerprint: str
    state: str
    enrolled_at: datetime
    revoked_at: datetime | None


class DeviceAttestationChallenge(BaseModel):
    """One-time server challenge for an explicitly enrolled device identity key.

    This proves possession of an enrolled Ed25519 device key. It deliberately does not claim
    TPM, Secure Enclave, measured boot, OS integrity or other hardware-rooted attestation.
    Those require platform-specific verifiers and evidence formats outside this contract.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    challenge_id: str = Field(min_length=20, max_length=128)
    challenge_nonce: str = Field(min_length=32, max_length=160)
    device_id: str = Field(min_length=16, max_length=128)
    command_id: str = Field(min_length=16, max_length=128)
    platform: str = Field(min_length=3, max_length=16)
    executor_id: str = Field(min_length=3, max_length=160)
    device_key_id: str = Field(min_length=3, max_length=80)
    issued_at: datetime
    expires_at: datetime

    @field_validator("challenge_id")
    @classmethod
    def validate_challenge_id(cls, value: str) -> str:
        if not _CHALLENGE_ID.fullmatch(value):
            raise ValueError("Aura Sec attestation challenge identity is invalid")
        return value

    @field_validator("challenge_nonce")
    @classmethod
    def validate_challenge_nonce(cls, value: str) -> str:
        if not _NONCE.fullmatch(value):
            raise ValueError("Aura Sec attestation challenge nonce is invalid")
        return value

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str) -> str:
        return _device_id(value)

    @field_validator("command_id")
    @classmethod
    def validate_command_id(cls, value: str) -> str:
        return _command_id(value)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return _platform(value)

    @field_validator("executor_id")
    @classmethod
    def validate_executor_id(cls, value: str) -> str:
        return _executor_id(value)

    @field_validator("device_key_id")
    @classmethod
    def validate_device_key_id(cls, value: str) -> str:
        return _key_id(value)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        return _utc(value)


class DeviceAttestationAssertion(BaseModel):
    """Device-key possession assertion over one exact server challenge."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    attestation_kind: str = Field(default="device-key-possession", pattern=r"^device-key-possession$")
    challenge_id: str = Field(min_length=20, max_length=128)
    challenge_nonce: str = Field(min_length=32, max_length=160)
    device_id: str = Field(min_length=16, max_length=128)
    command_id: str = Field(min_length=16, max_length=128)
    platform: str = Field(min_length=3, max_length=16)
    executor_id: str = Field(min_length=3, max_length=160)
    device_key_id: str = Field(min_length=3, max_length=80)
    key_algorithm: str = Field(default="ed25519", pattern=r"^ed25519$")
    public_key_fingerprint: str = Field(min_length=64, max_length=64)
    attested_at: datetime
    payload_digest: str = Field(min_length=64, max_length=64)
    signature_b64: str = Field(min_length=80, max_length=128)

    @field_validator("challenge_id")
    @classmethod
    def validate_challenge_id(cls, value: str) -> str:
        if not _CHALLENGE_ID.fullmatch(value):
            raise ValueError("Aura Sec attestation challenge identity is invalid")
        return value

    @field_validator("challenge_nonce")
    @classmethod
    def validate_challenge_nonce(cls, value: str) -> str:
        if not _NONCE.fullmatch(value):
            raise ValueError("Aura Sec attestation challenge nonce is invalid")
        return value

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str) -> str:
        return _device_id(value)

    @field_validator("command_id")
    @classmethod
    def validate_command_id(cls, value: str) -> str:
        return _command_id(value)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return _platform(value)

    @field_validator("executor_id")
    @classmethod
    def validate_executor_id(cls, value: str) -> str:
        return _executor_id(value)

    @field_validator("device_key_id")
    @classmethod
    def validate_device_key_id(cls, value: str) -> str:
        return _key_id(value)

    @field_validator("public_key_fingerprint", "payload_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        digest = str(value or "").strip().lower()
        if not _HEX_256.fullmatch(digest):
            raise ValueError("Aura Sec device-trust digest must be lowercase SHA-256 hex")
        return digest

    @field_validator("attested_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        return _utc(value)

    def canonical_payload(self) -> bytes:
        payload = {
            "attestation_kind": self.attestation_kind,
            "attested_at": self.attested_at.isoformat(),
            "challenge_id": self.challenge_id,
            "challenge_nonce": self.challenge_nonce,
            "command_id": self.command_id,
            "device_id": self.device_id,
            "device_key_id": self.device_key_id,
            "executor_id": self.executor_id,
            "key_algorithm": self.key_algorithm,
            "platform": self.platform,
            "public_key_fingerprint": self.public_key_fingerprint,
            "schema_version": self.schema_version,
        }
        return (
            _ASSERTION_DOMAIN
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")


@dataclass(frozen=True)
class VerifiedDeviceAttestation:
    challenge_id: str
    device_id: str
    command_id: str
    platform: str
    executor_id: str
    device_key_id: str
    public_key_fingerprint: str
    evidence_digest: str
    consumed_at: datetime


@dataclass(frozen=True)
class AttestedNativePlatformDispatchResult:
    attestation: VerifiedDeviceAttestation
    native: NativePlatformDispatchResult


class SelfHostedEd25519DeviceAttestor:
    """Local device-key signer.

    Production code supplies the private key from the device's protected key store. This helper
    never generates, persists or exports a long-lived private key implicitly.
    """

    def __init__(self, private_key: Ed25519PrivateKey, *, key_id: str):
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("Aura Sec device attestor requires an Ed25519 private key")
        self.key_id = _key_id(key_id)
        self._private_key = private_key
        self.public_key = private_key.public_key()
        self.public_key_fingerprint = public_key_fingerprint(self.public_key)

    def public_key_raw(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign_challenge(
        self,
        challenge: DeviceAttestationChallenge,
        *,
        attested_at: datetime | None = None,
    ) -> DeviceAttestationAssertion:
        if not isinstance(challenge, DeviceAttestationChallenge):
            challenge = DeviceAttestationChallenge.model_validate(challenge)
        if challenge.device_key_id != self.key_id:
            raise PermissionError("Aura Sec challenge targets a different device identity key")
        timestamp = _utc(attested_at)
        unsigned = DeviceAttestationAssertion(
            challenge_id=challenge.challenge_id,
            challenge_nonce=challenge.challenge_nonce,
            device_id=challenge.device_id,
            command_id=challenge.command_id,
            platform=challenge.platform,
            executor_id=challenge.executor_id,
            device_key_id=challenge.device_key_id,
            public_key_fingerprint=self.public_key_fingerprint,
            attested_at=timestamp,
            payload_digest="0" * 64,
            signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
        )
        payload = unsigned.canonical_payload()
        digest = hashlib.sha256(payload).hexdigest()
        signature = self._private_key.sign(payload)
        return unsigned.model_copy(
            update={
                "payload_digest": digest,
                "signature_b64": base64.b64encode(signature).decode("ascii"),
            }
        )


class AuraSecDeviceAttestationStore:
    """Durable device identity and one-time challenge store.

    Valid assertions are consumed atomically under `BEGIN IMMEDIATE`; a successfully consumed
    challenge can never authorize another native execution. Revoked device keys cannot satisfy
    existing challenges. Raw challenge nonces are not persisted: only their SHA-256 digests are.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS aura_sec_device_identity_keys (
                    device_id TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    executor_id TEXT NOT NULL,
                    public_key BLOB NOT NULL,
                    public_key_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active', 'revoked')),
                    enrolled_at TEXT NOT NULL,
                    revoked_at TEXT,
                    PRIMARY KEY(device_id, key_id)
                );

                CREATE TABLE IF NOT EXISTS aura_sec_device_attestation_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    nonce_digest TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    executor_id TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(device_id, key_id)
                        REFERENCES aura_sec_device_identity_keys(device_id, key_id)
                );

                CREATE INDEX IF NOT EXISTS idx_aura_sec_attestation_device_command
                    ON aura_sec_device_attestation_challenges(device_id, command_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def enroll_device_key(
        self,
        *,
        device_id: str,
        key_id: str,
        platform: str,
        executor_id: str,
        public_key: bytes | Ed25519PublicKey,
        now: datetime | None = None,
    ) -> DeviceIdentityRegistration:
        device = _device_id(device_id)
        key = _key_id(key_id)
        selected_platform = _platform(platform)
        executor = _executor_id(executor_id)
        enrolled_at = _utc(now)
        raw_key = _public_key_raw(public_key)
        fingerprint = public_key_fingerprint(_public_key(raw_key))

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (device, key),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["platform"] == selected_platform
                    and existing["executor_id"] == executor
                    and secrets.compare_digest(existing["public_key_fingerprint"], fingerprint)
                    and bytes(existing["public_key"]) == raw_key
                )
                if not same:
                    conn.execute("ROLLBACK")
                    raise PermissionError(
                        "Aura Sec device key id is already bound to different trust material"
                    )
                if existing["state"] != "active":
                    conn.execute("ROLLBACK")
                    raise PermissionError(
                        "Aura Sec revoked device key ids cannot be silently reactivated"
                    )
                conn.execute("COMMIT")
                return self._registration(existing)

            conn.execute(
                """
                INSERT INTO aura_sec_device_identity_keys (
                    device_id, key_id, platform, executor_id, public_key,
                    public_key_fingerprint, state, enrolled_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL)
                """,
                (
                    device,
                    key,
                    selected_platform,
                    executor,
                    raw_key,
                    fingerprint,
                    _iso(enrolled_at),
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (device, key),
            ).fetchone()
            conn.execute("COMMIT")
            return self._registration(row)

    def revoke_device_key(
        self,
        *,
        device_id: str,
        key_id: str,
        now: datetime | None = None,
    ) -> DeviceIdentityRegistration:
        device = _device_id(device_id)
        key = _key_id(key_id)
        revoked_at = _utc(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (device, key),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise ValueError("Aura Sec device identity key was not found")
            if row["state"] == "active":
                conn.execute(
                    """
                    UPDATE aura_sec_device_identity_keys
                    SET state = 'revoked', revoked_at = ?
                    WHERE device_id = ? AND key_id = ? AND state = 'active'
                    """,
                    (_iso(revoked_at), device, key),
                )
            row = conn.execute(
                """
                SELECT * FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (device, key),
            ).fetchone()
            conn.execute("COMMIT")
            return self._registration(row)

    @staticmethod
    def _registration(row: sqlite3.Row) -> DeviceIdentityRegistration:
        return DeviceIdentityRegistration(
            device_id=str(row["device_id"]),
            key_id=str(row["key_id"]),
            platform=str(row["platform"]),
            executor_id=str(row["executor_id"]),
            public_key_fingerprint=str(row["public_key_fingerprint"]),
            state=str(row["state"]),
            enrolled_at=_parse_time(str(row["enrolled_at"])),
            revoked_at=(
                _parse_time(str(row["revoked_at"])) if row["revoked_at"] is not None else None
            ),
        )

    def issue_challenge(
        self,
        *,
        device_id: str,
        command_id: str,
        platform: str,
        executor_id: str,
        device_key_id: str,
        not_after: datetime,
        ttl_seconds: int = 90,
        now: datetime | None = None,
    ) -> DeviceAttestationChallenge:
        device = _device_id(device_id)
        command = _command_id(command_id)
        selected_platform = _platform(platform)
        executor = _executor_id(executor_id)
        key = _key_id(device_key_id)
        current = _utc(now)
        command_deadline = _utc(not_after)
        ttl = int(ttl_seconds)
        if not 30 <= ttl <= 300:
            raise ValueError("Aura Sec attestation challenge TTL must be between 30 and 300 seconds")
        expires = min(current + timedelta(seconds=ttl), command_deadline)
        if expires <= current + timedelta(seconds=5):
            raise PermissionError("Aura Sec command expires too soon for a device attestation challenge")

        challenge_id = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(32)
        nonce_digest = hashlib.sha256(nonce.encode("ascii")).hexdigest()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            identity = conn.execute(
                """
                SELECT * FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (device, key),
            ).fetchone()
            if identity is None:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device identity key is not enrolled")
            if identity["state"] != "active":
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device identity key is revoked")
            if identity["platform"] != selected_platform:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device identity is enrolled for a different platform")
            if identity["executor_id"] != executor:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device identity is enrolled for a different executor")

            conn.execute(
                """
                INSERT INTO aura_sec_device_attestation_challenges (
                    challenge_id, nonce_digest, device_id, command_id, platform,
                    executor_id, key_id, issued_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    challenge_id,
                    nonce_digest,
                    device,
                    command,
                    selected_platform,
                    executor,
                    key,
                    _iso(current),
                    _iso(expires),
                ),
            )
            conn.execute("COMMIT")

        return DeviceAttestationChallenge(
            challenge_id=challenge_id,
            challenge_nonce=nonce,
            device_id=device,
            command_id=command,
            platform=selected_platform,
            executor_id=executor,
            device_key_id=key,
            issued_at=current,
            expires_at=expires,
        )

    def verify_and_consume(
        self,
        assertion: DeviceAttestationAssertion,
        *,
        expected_device_id: str,
        expected_command_id: str,
        expected_platform: str,
        expected_executor_id: str,
        now: datetime | None = None,
        future_skew_seconds: int = 30,
    ) -> VerifiedDeviceAttestation:
        if not isinstance(assertion, DeviceAttestationAssertion):
            assertion = DeviceAttestationAssertion.model_validate(assertion)
        device = _device_id(expected_device_id)
        command = _command_id(expected_command_id)
        platform = _platform(expected_platform)
        executor = _executor_id(expected_executor_id)
        current = _utc(now)
        skew = int(future_skew_seconds)
        if not 0 <= skew <= 120:
            raise ValueError("Aura Sec device-attestation clock-skew allowance is invalid")

        if not secrets.compare_digest(assertion.device_id, device):
            raise PermissionError("Aura Sec attestation targets a different device")
        if not secrets.compare_digest(assertion.command_id, command):
            raise PermissionError("Aura Sec attestation targets a different command")
        if assertion.platform != platform:
            raise PermissionError("Aura Sec attestation targets a different platform")
        if not secrets.compare_digest(assertion.executor_id, executor):
            raise PermissionError("Aura Sec attestation targets a different native executor")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            challenge = conn.execute(
                """
                SELECT * FROM aura_sec_device_attestation_challenges
                WHERE challenge_id = ?
                """,
                (assertion.challenge_id,),
            ).fetchone()
            if challenge is None:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation challenge is unknown")
            if challenge["consumed_at"] is not None:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation challenge was already consumed")

            issued_at = _parse_time(str(challenge["issued_at"]))
            expires_at = _parse_time(str(challenge["expires_at"]))
            if current >= expires_at:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation challenge has expired")

            bound_fields = {
                "device_id": assertion.device_id,
                "command_id": assertion.command_id,
                "platform": assertion.platform,
                "executor_id": assertion.executor_id,
                "key_id": assertion.device_key_id,
            }
            for column, value in bound_fields.items():
                if not secrets.compare_digest(str(challenge[column]), str(value)):
                    conn.execute("ROLLBACK")
                    raise PermissionError(
                        f"Aura Sec device-attestation challenge {column} binding does not match"
                    )
            nonce_digest = hashlib.sha256(assertion.challenge_nonce.encode("ascii")).hexdigest()
            if not secrets.compare_digest(str(challenge["nonce_digest"]), nonce_digest):
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation challenge nonce does not match")

            if assertion.attested_at < issued_at - timedelta(seconds=skew):
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device attestation predates its challenge")
            if assertion.attested_at > current + timedelta(seconds=skew):
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device attestation is too far in the future")
            if assertion.attested_at >= expires_at:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device attestation was created after challenge expiry")

            identity = conn.execute(
                """
                SELECT * FROM aura_sec_device_identity_keys
                WHERE device_id = ? AND key_id = ?
                """,
                (device, assertion.device_key_id),
            ).fetchone()
            if identity is None or identity["state"] != "active":
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device identity key is not actively trusted")
            if identity["platform"] != platform or identity["executor_id"] != executor:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device identity binding does not match native execution")

            fingerprint = str(identity["public_key_fingerprint"])
            if not secrets.compare_digest(fingerprint, assertion.public_key_fingerprint):
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device public-key fingerprint does not match enrollment")

            payload = assertion.canonical_payload()
            digest = hashlib.sha256(payload).hexdigest()
            if not secrets.compare_digest(digest, assertion.payload_digest):
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation payload digest does not match")
            try:
                signature = base64.b64decode(assertion.signature_b64, validate=True)
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation signature is not valid base64") from exc
            if len(signature) != 64:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation signature length is invalid")
            try:
                public_key = Ed25519PublicKey.from_public_bytes(bytes(identity["public_key"]))
                public_key.verify(signature, payload)
            except InvalidSignature as exc:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation signature verification failed") from exc
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation verification failed closed") from exc

            consumed_at = current
            updated = conn.execute(
                """
                UPDATE aura_sec_device_attestation_challenges
                SET consumed_at = ?
                WHERE challenge_id = ? AND consumed_at IS NULL
                """,
                (_iso(consumed_at), assertion.challenge_id),
            )
            if updated.rowcount != 1:
                conn.execute("ROLLBACK")
                raise PermissionError("Aura Sec device-attestation challenge replay was detected")
            conn.execute("COMMIT")

        return VerifiedDeviceAttestation(
            challenge_id=assertion.challenge_id,
            device_id=device,
            command_id=command,
            platform=platform,
            executor_id=executor,
            device_key_id=assertion.device_key_id,
            public_key_fingerprint=fingerprint,
            evidence_digest=digest,
            consumed_at=consumed_at,
        )


class AuraSecAttestedNativePlatformExecutor:
    """Fail-closed device-trust admission in front of native Aura Sec execution.

    The server-signed command is verified before a challenge is issued or consumed. A valid
    one-time device-key assertion is then consumed before the existing native executor may reserve
    or perform any OS side effect. The native executor still independently re-verifies the signed
    command, sequence anti-rollback and execute-once state.
    """

    def __init__(
        self,
        native_executor: AuraSecNativePlatformExecutor,
        attestation_store: AuraSecDeviceAttestationStore,
    ):
        if not isinstance(native_executor, AuraSecNativePlatformExecutor):
            raise TypeError("Aura Sec attested execution requires AuraSecNativePlatformExecutor")
        if not isinstance(attestation_store, AuraSecDeviceAttestationStore):
            raise TypeError("Aura Sec attested execution requires AuraSecDeviceAttestationStore")
        self.native_executor = native_executor
        self.attestation_store = attestation_store
        # Snapshot the executor identities already frozen by AuraSecNativePlatformExecutor.
        # This package-internal read avoids trusting a mutable adapter attribute at attestation time.
        self._executor_ids = dict(native_executor._executor_ids)
        if not self._executor_ids:
            raise ValueError("Aura Sec attested execution requires at least one native adapter")

    def _registered_executor_id(self, platform: str) -> str:
        selected = _platform(platform)
        executor = self._executor_ids.get(selected)
        if executor is None:
            raise PermissionError(
                f"No trusted Aura Sec native adapter is registered for {selected}"
            )
        return executor

    def enroll_device_key(
        self,
        *,
        device_id: str,
        device_key_id: str,
        platform: str,
        public_key: bytes | Ed25519PublicKey,
        now: datetime | None = None,
    ) -> DeviceIdentityRegistration:
        selected = _platform(platform)
        return self.attestation_store.enroll_device_key(
            device_id=device_id,
            key_id=device_key_id,
            platform=selected,
            executor_id=self._registered_executor_id(selected),
            public_key=public_key,
            now=now,
        )

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
    ) -> DeviceAttestationChallenge:
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)
        verify_signed_security_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        selected = _platform(platform)
        executor = self._registered_executor_id(selected)
        return self.attestation_store.issue_challenge(
            device_id=command.device_id,
            command_id=command.command_id,
            platform=selected,
            executor_id=executor,
            device_key_id=device_key_id,
            not_after=command.expires_at,
            ttl_seconds=ttl_seconds,
            now=current,
        )

    def dispatch(
        self,
        command: SignedSecurityCommand,
        assertion: DeviceAttestationAssertion,
        *,
        trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
        expected_device_id: str,
        platform: str,
        now: datetime | None = None,
    ) -> AttestedNativePlatformDispatchResult:
        current = _utc(now)
        if not isinstance(command, SignedSecurityCommand):
            command = SignedSecurityCommand.model_validate(command)
        verify_signed_security_command(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            now=current,
        )
        selected = _platform(platform)
        executor = self._registered_executor_id(selected)
        verified = self.attestation_store.verify_and_consume(
            assertion,
            expected_device_id=command.device_id,
            expected_command_id=command.command_id,
            expected_platform=selected,
            expected_executor_id=executor,
            now=current,
        )
        native = self.native_executor.dispatch(
            command,
            trusted_public_keys=trusted_public_keys,
            expected_device_id=expected_device_id,
            platform=selected,
            now=current,
        )
        return AttestedNativePlatformDispatchResult(attestation=verified, native=native)


__all__ = [
    "AttestedNativePlatformDispatchResult",
    "AuraSecAttestedNativePlatformExecutor",
    "AuraSecDeviceAttestationStore",
    "DeviceAttestationAssertion",
    "DeviceAttestationChallenge",
    "DeviceIdentityRegistration",
    "SelfHostedEd25519DeviceAttestor",
    "VerifiedDeviceAttestation",
]
