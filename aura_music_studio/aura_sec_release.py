from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")


class ReleaseManifestError(ValueError):
    pass


@dataclass(frozen=True)
class AuraSecReleaseManifest:
    schema_version: int
    release_sequence: int
    product: str
    channel: str
    platform: str
    architecture: str
    version: str
    minimum_os: str
    artifact_url: str
    artifact_sha256: str
    artifact_size_bytes: int
    issued_at: str
    expires_at: str
    signing_key_id: str
    signature_b64: str
    sbom_url: str
    provenance_url: str

    @classmethod
    def from_dict(cls, payload: dict) -> "AuraSecReleaseManifest":
        if not isinstance(payload, dict):
            raise ReleaseManifestError("Release manifest must be an object")
        required = {
            "schema_version",
            "release_sequence",
            "product",
            "channel",
            "platform",
            "architecture",
            "version",
            "minimum_os",
            "artifact_url",
            "artifact_sha256",
            "artifact_size_bytes",
            "issued_at",
            "expires_at",
            "signing_key_id",
            "signature_b64",
            "sbom_url",
            "provenance_url",
        }
        missing = required - payload.keys()
        if missing:
            raise ReleaseManifestError(f"Release manifest missing fields: {', '.join(sorted(missing))}")
        unexpected = payload.keys() - required
        if unexpected:
            raise ReleaseManifestError(f"Unexpected release manifest fields: {', '.join(sorted(unexpected))}")
        try:
            return cls(
                schema_version=int(payload["schema_version"]),
                release_sequence=int(payload["release_sequence"]),
                product=str(payload["product"]),
                channel=str(payload["channel"]),
                platform=str(payload["platform"]),
                architecture=str(payload["architecture"]),
                version=str(payload["version"]),
                minimum_os=str(payload["minimum_os"]),
                artifact_url=str(payload["artifact_url"]),
                artifact_sha256=str(payload["artifact_sha256"]).lower(),
                artifact_size_bytes=int(payload["artifact_size_bytes"]),
                issued_at=str(payload["issued_at"]),
                expires_at=str(payload["expires_at"]),
                signing_key_id=str(payload["signing_key_id"]),
                signature_b64=str(payload["signature_b64"]),
                sbom_url=str(payload["sbom_url"]),
                provenance_url=str(payload["provenance_url"]),
            )
        except (TypeError, ValueError) as exc:
            raise ReleaseManifestError("Release manifest contains invalid field types") from exc

    def signed_payload(self) -> bytes:
        parts = (
            str(self.schema_version),
            str(self.release_sequence),
            self.product,
            self.channel,
            self.platform,
            self.architecture,
            self.version,
            self.minimum_os,
            self.artifact_url,
            self.artifact_sha256,
            str(self.artifact_size_bytes),
            self.issued_at,
            self.expires_at,
            self.signing_key_id,
            self.sbom_url,
            self.provenance_url,
        )
        if any("\n" in part or "\r" in part for part in parts):
            raise ReleaseManifestError("Manifest canonical fields must not contain newlines")
        return ("AURA-SEC-RELEASE-V2\n" + "\n".join(parts) + "\n").encode("utf-8")


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseManifestError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReleaseManifestError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_https(url: str, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ReleaseManifestError(f"{field} must be an absolute HTTPS URL without embedded credentials")
    if parsed.fragment:
        raise ReleaseManifestError(f"{field} must not contain a URL fragment")


def validate_release_manifest(
    manifest: AuraSecReleaseManifest,
    *,
    trusted_key_ids: set[str],
    signature_verifier: Callable[[str, bytes, bytes], bool] | None,
    now: datetime | None = None,
    minimum_release_sequence: int = 0,
    trusted_sequence_sha256: str | None = None,
) -> dict:
    if manifest.schema_version != 2:
        raise ReleaseManifestError("Unsupported Aura Sec release manifest schema; signed sequence metadata is required")
    if not 1 <= int(manifest.release_sequence) <= 2**63 - 1:
        raise ReleaseManifestError("Release sequence must be a positive 63-bit integer")
    if not 0 <= int(minimum_release_sequence) <= 2**63 - 1:
        raise ReleaseManifestError("Minimum trusted release sequence is invalid")
    if trusted_sequence_sha256 is not None:
        trusted_sequence_sha256 = trusted_sequence_sha256.strip().lower()
        if not _SHA256_RE.fullmatch(trusted_sequence_sha256):
            raise ReleaseManifestError("Trusted sequence artifact SHA-256 is invalid")
    if manifest.product != "aura-sec":
        raise ReleaseManifestError("Release manifest product mismatch")
    if manifest.channel not in {"stable", "beta", "canary"}:
        raise ReleaseManifestError("Invalid release channel")
    if manifest.platform not in {"windows", "macos", "linux", "android", "ios", "browser", "chromeos"}:
        raise ReleaseManifestError("Unsupported release platform")
    if not re.fullmatch(r"[A-Za-z0-9._+-]{2,40}", manifest.architecture):
        raise ReleaseManifestError("Invalid release architecture")
    if not _VERSION_RE.fullmatch(manifest.version):
        raise ReleaseManifestError("Invalid release version")
    if not manifest.minimum_os.strip() or len(manifest.minimum_os) > 120:
        raise ReleaseManifestError("Invalid minimum OS declaration")
    if not _SHA256_RE.fullmatch(manifest.artifact_sha256):
        raise ReleaseManifestError("Artifact SHA-256 must contain exactly 64 hexadecimal characters")
    if not 1 <= manifest.artifact_size_bytes <= 20 * 1024 * 1024 * 1024:
        raise ReleaseManifestError("Artifact size is outside the permitted release range")
    if not _KEY_ID_RE.fullmatch(manifest.signing_key_id):
        raise ReleaseManifestError("Invalid signing key id")
    if manifest.signing_key_id not in trusted_key_ids:
        raise ReleaseManifestError("Release signing key is not trusted for this client")
    for field, url in (
        ("artifact_url", manifest.artifact_url),
        ("sbom_url", manifest.sbom_url),
        ("provenance_url", manifest.provenance_url),
    ):
        _require_https(url, field)
    issued = _parse_time(manifest.issued_at, "issued_at")
    expires = _parse_time(manifest.expires_at, "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires <= issued:
        raise ReleaseManifestError("Release manifest expiry must be after issuance")
    if current < issued:
        raise ReleaseManifestError("Release manifest is not valid yet")
    if current >= expires:
        raise ReleaseManifestError("Release manifest has expired")
    if expires - issued > timedelta(days=31):
        raise ReleaseManifestError("Release manifest validity window is too long")
    try:
        signature = base64.b64decode(manifest.signature_b64, validate=True)
    except Exception as exc:
        raise ReleaseManifestError("Release signature is not valid base64") from exc
    if not 32 <= len(signature) <= 1024:
        raise ReleaseManifestError("Release signature length is invalid")
    if signature_verifier is None:
        raise ReleaseManifestError("No Aura Sec release signature verifier is configured")
    try:
        verified = bool(signature_verifier(manifest.signing_key_id, manifest.signed_payload(), signature))
    except Exception as exc:
        raise ReleaseManifestError("Release signature verification failed closed") from exc
    if not verified:
        raise ReleaseManifestError("Release signature is invalid")
    minimum = int(minimum_release_sequence)
    if manifest.release_sequence < minimum:
        raise ReleaseManifestError("Release rollback rejected: signed sequence is older than trusted client state")
    if minimum > 0 and manifest.release_sequence == minimum:
        if trusted_sequence_sha256 is None:
            raise ReleaseManifestError("Same-sequence release requires the previously trusted artifact SHA-256")
        if not secrets_compare_digest(manifest.artifact_sha256, trusted_sequence_sha256):
            raise ReleaseManifestError("Release equivocation rejected: the trusted sequence points to a different artifact")
    return {
        "verified": True,
        "downloadable": True,
        "product": manifest.product,
        "channel": manifest.channel,
        "platform": manifest.platform,
        "architecture": manifest.architecture,
        "version": manifest.version,
        "release_sequence": manifest.release_sequence,
        "artifact_url": manifest.artifact_url,
        "artifact_sha256": manifest.artifact_sha256,
        "artifact_size_bytes": manifest.artifact_size_bytes,
        "signing_key_id": manifest.signing_key_id,
        "expires_at": manifest.expires_at,
        "sbom_url": manifest.sbom_url,
        "provenance_url": manifest.provenance_url,
    }


def secrets_compare_digest(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(left, right)


__all__ = ["AuraSecReleaseManifest", "ReleaseManifestError", "validate_release_manifest"]
