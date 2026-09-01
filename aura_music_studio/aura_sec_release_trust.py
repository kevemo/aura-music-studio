from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .aura_sec_release import (
    AuraSecReleaseManifest,
    ReleaseManifestError,
    validate_release_manifest,
)

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_MAX_MANIFEST_LIFETIME = timedelta(days=7)


class ReleaseAdmissionError(ReleaseManifestError):
    """Aura Sec local release admission failed closed."""


@dataclass(frozen=True)
class ReleaseAdmissionState:
    channel: str
    platform: str
    architecture: str
    release_sequence: int
    version: str
    manifest_sha256: str
    artifact_sha256: str
    artifact_size_bytes: int
    signing_key_id: str
    admitted_at: datetime


@dataclass(frozen=True)
class ReleaseAdmissionDecision:
    status: str
    state: ReleaseAdmissionState

    @property
    def accepted(self) -> bool:
        return self.status in {"accepted", "replay"}

    @property
    def replay(self) -> bool:
        return self.status == "replay"


@dataclass(frozen=True)
class ReleaseAdmissionEvent:
    event_id: int
    channel: str
    platform: str
    architecture: str
    release_sequence: int
    version: str
    manifest_sha256: str
    decision: str
    occurred_at: datetime


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _manifest_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseAdmissionError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReleaseAdmissionError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_semver(value: str) -> tuple[int, int, int, tuple[str, ...] | None]:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise ReleaseAdmissionError(
            "Aura Sec release version must use strict semantic versioning (major.minor.patch)"
        )
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else None
    if prerelease:
        for identifier in prerelease:
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ReleaseAdmissionError(
                    "Aura Sec semantic-version numeric prerelease identifiers cannot have leading zeroes"
                )
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def _compare_prerelease(left: tuple[str, ...] | None, right: tuple[str, ...] | None) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return 1
    if right is None:
        return -1
    for left_item, right_item in zip(left, right):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_item) < int(right_item) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_item < right_item else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def _compare_semver(left: str, right: str) -> int:
    left_major, left_minor, left_patch, left_pre = _parse_semver(left)
    right_major, right_minor, right_patch, right_pre = _parse_semver(right)
    left_core = (left_major, left_minor, left_patch)
    right_core = (right_major, right_minor, right_patch)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    return _compare_prerelease(left_pre, right_pre)


def _coerce_public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if not isinstance(value, (bytes, bytearray)) or len(value) != 32:
        raise ValueError("Aura Sec trusted release public keys must be raw 32-byte Ed25519 keys")
    try:
        return Ed25519PublicKey.from_public_bytes(bytes(value))
    except Exception as exc:
        raise ValueError("Aura Sec trusted release public key is invalid") from exc


def _hash_local_artifact(path: Path) -> tuple[str, int]:
    if path.is_symlink():
        raise ReleaseAdmissionError("Aura Sec release artifact must not be a symbolic link")
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise ReleaseAdmissionError("Aura Sec release artifact could not be opened") from exc
    digest = hashlib.sha256()
    total = 0
    with handle:
        try:
            before = os.fstat(handle.fileno())
        except OSError as exc:
            raise ReleaseAdmissionError("Aura Sec release artifact metadata could not be read") from exc
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseAdmissionError("Aura Sec release artifact must be a regular file")
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            total += len(block)
        try:
            after = os.fstat(handle.fileno())
        except OSError as exc:
            raise ReleaseAdmissionError("Aura Sec release artifact metadata could not be rechecked") from exc
    if total != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise ReleaseAdmissionError("Aura Sec release artifact changed while it was being verified")
    return digest.hexdigest(), total


class AuraSecReleaseAdmissionStore:
    """Durable local admission boundary for signed Aura Sec release artifacts.

    Release-signing keys are supplied explicitly and are a separate trust domain from
    server-command signing keys. This store verifies the existing signed release manifest
    with Ed25519, validates the already-local artifact's SHA-256 and byte size, and records
    a monotonic high-water mark per channel/platform/architecture. A lower sequence or a
    lower semantic version fails closed; reuse of a sequence for different signed metadata
    is rejected as equivocation. Exact re-admission of the same signed release is idempotent.

    This class does NOT download artifacts, execute installers, modify operating-system
    services, perform native rollback, distribute trust bundles, configure an HSM/KMS,
    or assert platform code-signing/notarization. A future native updater must independently
    preserve the admitted artifact identity through installation and platform verification.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        trusted_release_public_keys: Mapping[str, bytes | Ed25519PublicKey],
    ):
        if not trusted_release_public_keys:
            raise ValueError("Aura Sec release admission requires at least one trusted release public key")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._trusted_keys = {
            str(key_id): _coerce_public_key(public_key)
            for key_id, public_key in trusted_release_public_keys.items()
        }
        if any(not key_id for key_id in self._trusted_keys):
            raise ValueError("Aura Sec trusted release key ids must not be empty")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_sec_release_admission_high_water (
                    channel TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    release_sequence INTEGER NOT NULL CHECK(release_sequence>=1),
                    version TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    artifact_size_bytes INTEGER NOT NULL CHECK(artifact_size_bytes>=1),
                    signing_key_id TEXT NOT NULL,
                    admitted_at TEXT NOT NULL,
                    PRIMARY KEY(channel, platform, architecture)
                );

                CREATE TABLE IF NOT EXISTS aura_sec_release_admission_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    release_sequence INTEGER NOT NULL,
                    version TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('accepted','replay')),
                    occurred_at TEXT NOT NULL
                );
                """
            )

    def _verify_signature(self, key_id: str, payload: bytes, signature: bytes) -> bool:
        public_key = self._trusted_keys.get(key_id)
        if public_key is None:
            return False
        try:
            public_key.verify(signature, payload)
        except InvalidSignature:
            return False
        except Exception:
            return False
        return True

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> ReleaseAdmissionState:
        return ReleaseAdmissionState(
            channel=str(row["channel"]),
            platform=str(row["platform"]),
            architecture=str(row["architecture"]),
            release_sequence=int(row["release_sequence"]),
            version=str(row["version"]),
            manifest_sha256=str(row["manifest_sha256"]),
            artifact_sha256=str(row["artifact_sha256"]),
            artifact_size_bytes=int(row["artifact_size_bytes"]),
            signing_key_id=str(row["signing_key_id"]),
            admitted_at=_from_iso(str(row["admitted_at"])),
        )

    def high_water(
        self, *, channel: str, platform: str, architecture: str
    ) -> ReleaseAdmissionState | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM aura_sec_release_admission_high_water
                   WHERE channel=? AND platform=? AND architecture=?""",
                (channel, platform, architecture),
            ).fetchone()
        return self._state_from_row(row) if row is not None else None

    def audit_events(self) -> list[ReleaseAdmissionEvent]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,channel,platform,architecture,release_sequence,version,
                          manifest_sha256,decision,occurred_at
                   FROM aura_sec_release_admission_events ORDER BY id"""
            ).fetchall()
        return [
            ReleaseAdmissionEvent(
                event_id=int(row["id"]),
                channel=str(row["channel"]),
                platform=str(row["platform"]),
                architecture=str(row["architecture"]),
                release_sequence=int(row["release_sequence"]),
                version=str(row["version"]),
                manifest_sha256=str(row["manifest_sha256"]),
                decision=str(row["decision"]),
                occurred_at=_from_iso(str(row["occurred_at"])),
            )
            for row in rows
        ]

    def admit_local_artifact(
        self,
        manifest: AuraSecReleaseManifest,
        artifact_path: str | Path,
        *,
        expected_channel: str,
        expected_platform: str,
        expected_architecture: str,
        now: datetime | None = None,
    ) -> ReleaseAdmissionDecision:
        current = _utc(now)
        expected = {
            "channel": str(expected_channel),
            "platform": str(expected_platform),
            "architecture": str(expected_architecture),
        }
        actual = {
            "channel": manifest.channel,
            "platform": manifest.platform,
            "architecture": manifest.architecture,
        }
        for field, expected_value in expected.items():
            if actual[field] != expected_value:
                raise ReleaseAdmissionError(
                    f"Aura Sec release {field} mismatch: manifest is not intended for this updater target"
                )

        # The existing manifest validator owns schema, product, HTTPS evidence URLs,
        # signature presence, release-sequence shape and baseline time validation.
        validate_release_manifest(
            manifest,
            trusted_key_ids=set(self._trusted_keys),
            signature_verifier=self._verify_signature,
            now=current,
        )

        issued = _manifest_time(manifest.issued_at, "issued_at")
        expires = _manifest_time(manifest.expires_at, "expires_at")
        if expires - issued > _MAX_MANIFEST_LIFETIME:
            raise ReleaseAdmissionError(
                "Aura Sec release admission requires manifests to expire within 7 days"
            )
        _parse_semver(manifest.version)

        artifact_sha256, artifact_size = _hash_local_artifact(Path(artifact_path))
        if artifact_size != manifest.artifact_size_bytes:
            raise ReleaseAdmissionError("Aura Sec release artifact byte size does not match signed metadata")
        if not secrets.compare_digest(artifact_sha256, manifest.artifact_sha256):
            raise ReleaseAdmissionError("Aura Sec release artifact SHA-256 does not match signed metadata")

        manifest_sha256 = hashlib.sha256(manifest.signed_payload()).hexdigest()
        target = (manifest.channel, manifest.platform, manifest.architecture)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT * FROM aura_sec_release_admission_high_water
                   WHERE channel=? AND platform=? AND architecture=?""",
                target,
            ).fetchone()
            if row is not None:
                previous = self._state_from_row(row)
                if manifest.release_sequence < previous.release_sequence:
                    raise ReleaseAdmissionError(
                        "Aura Sec release rollback rejected: sequence is below the durable local high-water mark"
                    )
                if manifest.release_sequence == previous.release_sequence:
                    if not secrets.compare_digest(manifest_sha256, previous.manifest_sha256):
                        raise ReleaseAdmissionError(
                            "Aura Sec release equivocation rejected: sequence was reused for different signed metadata"
                        )
                    con.execute(
                        """INSERT INTO aura_sec_release_admission_events(
                               channel,platform,architecture,release_sequence,version,
                               manifest_sha256,decision,occurred_at
                           ) VALUES(?,?,?,?,?,?,'replay',?)""",
                        (*target, manifest.release_sequence, manifest.version, manifest_sha256, _iso(current)),
                    )
                    return ReleaseAdmissionDecision(status="replay", state=previous)
                if _compare_semver(manifest.version, previous.version) < 0:
                    raise ReleaseAdmissionError(
                        "Aura Sec release downgrade rejected: semantic version is below the durable local high-water mark"
                    )

            con.execute(
                """INSERT INTO aura_sec_release_admission_high_water(
                       channel,platform,architecture,release_sequence,version,manifest_sha256,
                       artifact_sha256,artifact_size_bytes,signing_key_id,admitted_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(channel,platform,architecture) DO UPDATE SET
                       release_sequence=excluded.release_sequence,
                       version=excluded.version,
                       manifest_sha256=excluded.manifest_sha256,
                       artifact_sha256=excluded.artifact_sha256,
                       artifact_size_bytes=excluded.artifact_size_bytes,
                       signing_key_id=excluded.signing_key_id,
                       admitted_at=excluded.admitted_at""",
                (
                    *target,
                    manifest.release_sequence,
                    manifest.version,
                    manifest_sha256,
                    artifact_sha256,
                    artifact_size,
                    manifest.signing_key_id,
                    _iso(current),
                ),
            )
            con.execute(
                """INSERT INTO aura_sec_release_admission_events(
                       channel,platform,architecture,release_sequence,version,
                       manifest_sha256,decision,occurred_at
                   ) VALUES(?,?,?,?,?,?,'accepted',?)""",
                (*target, manifest.release_sequence, manifest.version, manifest_sha256, _iso(current)),
            )
            persisted = con.execute(
                """SELECT * FROM aura_sec_release_admission_high_water
                   WHERE channel=? AND platform=? AND architecture=?""",
                target,
            ).fetchone()
        if persisted is None:
            raise RuntimeError("Aura Sec release admission state was not persisted")
        return ReleaseAdmissionDecision(status="accepted", state=self._state_from_row(persisted))


__all__ = [
    "AuraSecReleaseAdmissionStore",
    "ReleaseAdmissionDecision",
    "ReleaseAdmissionError",
    "ReleaseAdmissionEvent",
    "ReleaseAdmissionState",
]
