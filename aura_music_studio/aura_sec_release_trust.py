from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_PLATFORMS = {"windows", "macos", "linux", "android", "ios"}
_ALLOWED_ARCHITECTURES = {"x64", "arm64", "universal"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Aura Sec release timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class AuraSecReleaseArtifact:
    platform: str
    architecture: str
    version: str
    download_url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class AuraSecReleaseManifest:
    release_id: str
    channel: str
    issued_at: datetime
    expires_at: datetime
    signer_id: str
    provenance_uri: str
    sbom_uri: str
    artifacts: tuple[AuraSecReleaseArtifact, ...]


@dataclass(frozen=True)
class VerifiedReleaseManifest:
    signer_id: str
    verifier_id: str
    manifest_digest: str
    signature_algorithm: str


def canonical_release_manifest_payload(manifest: AuraSecReleaseManifest) -> bytes:
    issued_at = _utc(manifest.issued_at)
    expires_at = _utc(manifest.expires_at)
    if expires_at <= issued_at:
        raise ValueError("Aura Sec release manifest expiry must follow issuance")
    if not manifest.release_id.strip() or not manifest.signer_id.strip():
        raise ValueError("Aura Sec release identity and signer are required")
    if manifest.channel not in {"stable", "beta"}:
        raise ValueError("Aura Sec release channel is invalid")
    if not manifest.provenance_uri.startswith("https://") or not manifest.sbom_uri.startswith("https://"):
        raise ValueError("Aura Sec provenance and SBOM references must use HTTPS")
    if not manifest.artifacts:
        raise ValueError("Aura Sec release manifest must contain at least one artifact")

    lines = [
        "AURA-SEC-RELEASE-MANIFEST-V1",
        manifest.release_id.strip(),
        manifest.channel,
        issued_at.isoformat(),
        expires_at.isoformat(),
        manifest.signer_id.strip(),
        manifest.provenance_uri.strip(),
        manifest.sbom_uri.strip(),
    ]
    seen_targets: set[tuple[str, str]] = set()
    for artifact in sorted(manifest.artifacts, key=lambda item: (item.platform, item.architecture, item.version)):
        platform = artifact.platform.strip().lower()
        architecture = artifact.architecture.strip().lower()
        digest = artifact.sha256.strip().lower()
        target = (platform, architecture)
        if platform not in _ALLOWED_PLATFORMS:
            raise ValueError("Unsupported Aura Sec release platform")
        if architecture not in _ALLOWED_ARCHITECTURES:
            raise ValueError("Unsupported Aura Sec release architecture")
        if target in seen_targets:
            raise ValueError("Aura Sec release manifest contains a duplicate platform target")
        seen_targets.add(target)
        if not artifact.version.strip() or len(artifact.version) > 80:
            raise ValueError("Aura Sec release version is invalid")
        if not artifact.download_url.startswith("https://"):
            raise ValueError("Aura Sec release artifacts must use HTTPS downloads")
        if not _HEX_256.fullmatch(digest):
            raise ValueError("Aura Sec release artifact SHA-256 is invalid")
        if artifact.size_bytes <= 0:
            raise ValueError("Aura Sec release artifact size must be positive")
        values = (
            platform,
            architecture,
            artifact.version.strip(),
            artifact.download_url.strip(),
            digest,
            str(artifact.size_bytes),
        )
        if any("\n" in value or "\r" in value for value in values):
            raise ValueError("Aura Sec release manifest fields must not contain newlines")
        lines.append("|".join(values))
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify_release_manifest_contract(
    manifest: AuraSecReleaseManifest,
    verification: VerifiedReleaseManifest,
    *,
    trusted_signers: Iterable[str],
    now: datetime,
) -> str:
    current = _utc(now)
    issued_at = _utc(manifest.issued_at)
    expires_at = _utc(manifest.expires_at)
    payload = canonical_release_manifest_payload(manifest)
    expected_digest = hashlib.sha256(payload).hexdigest()

    trusted = {item.strip() for item in trusted_signers if item and item.strip()}
    if manifest.signer_id not in trusted:
        raise PermissionError("Aura Sec release signer is not trusted")
    if verification.signer_id != manifest.signer_id:
        raise PermissionError("Aura Sec release verification signer does not match manifest")
    if verification.signature_algorithm not in {"ed25519", "p256", "rsa-pss-sha256"}:
        raise PermissionError("Aura Sec release signature algorithm is unsupported")
    if not verification.verifier_id.strip():
        raise PermissionError("Aura Sec release verifier identity is required")
    if verification.manifest_digest.strip().lower() != expected_digest:
        raise PermissionError("Aura Sec release signature evidence does not cover the exact manifest")
    if current < issued_at:
        raise PermissionError("Aura Sec release manifest is not yet valid")
    if current >= expires_at:
        raise PermissionError("Aura Sec release manifest has expired")
    return expected_digest


__all__ = [
    "AuraSecReleaseArtifact",
    "AuraSecReleaseManifest",
    "VerifiedReleaseManifest",
    "canonical_release_manifest_payload",
    "verify_release_manifest_contract",
]
