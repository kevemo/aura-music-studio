from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence

from .aura_sec_release import AuraSecReleaseManifest
from .aura_sec_release_trust import (
    AuraSecReleaseAdmissionStore,
    ReleaseAdmissionDecision,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NATIVE_PLATFORMS = {"windows", "macos", "linux"}
_MAX_EVIDENCE_AGE = timedelta(minutes=10)
_MAX_FUTURE_SKEW = timedelta(seconds=30)


class PlatformReleaseVerificationError(PermissionError):
    """Platform-native release authenticity evidence failed closed."""


@dataclass(frozen=True)
class PlatformReleaseEvidence:
    platform: str
    artifact_sha256: str
    artifact_size_bytes: int
    signing_identity: str
    verifier_id: str
    provider_request_id: str
    verified_at: datetime
    signature_valid: bool
    trust_chain_valid: bool
    revocation_checked: bool
    timestamp_valid: bool
    notarization_valid: bool | None = None
    package_signature_valid: bool | None = None


@dataclass(frozen=True)
class PlatformVerifiedReleaseAdmissionDecision:
    release: ReleaseAdmissionDecision
    platform_evidence: PlatformReleaseEvidence

    @property
    def accepted(self) -> bool:
        return self.release.accepted


class PlatformReleaseVerifier(Protocol):
    """Adapter implemented by platform-specific native verification code.

    Implementations are expected to use the operating system's supported authenticity
    mechanisms (for example Authenticode on Windows, code-signing/notarisation on macOS,
    or an approved package-signature policy on Linux). This protocol itself performs no
    shell execution and makes no claim that such an adapter is configured in production.
    """

    def verify_release(
        self,
        artifact_path: Path,
        manifest: AuraSecReleaseManifest,
        *,
        now: datetime,
    ) -> PlatformReleaseEvidence: ...


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def validate_platform_release_evidence(
    manifest: AuraSecReleaseManifest,
    evidence: PlatformReleaseEvidence,
    *,
    expected_signing_identities: Sequence[str],
    now: datetime | None = None,
) -> PlatformReleaseEvidence:
    """Validate adapter evidence without executing or installing the artifact."""

    current = _utc(now)
    if manifest.platform not in _NATIVE_PLATFORMS:
        raise PlatformReleaseVerificationError(
            "Aura Sec platform-native release verification is only defined for windows, macos and linux"
        )
    identities = {str(identity).strip() for identity in expected_signing_identities if str(identity).strip()}
    if not identities:
        raise PlatformReleaseVerificationError(
            "Aura Sec platform release verification requires an explicit signing-identity allowlist"
        )
    if evidence.platform != manifest.platform:
        raise PlatformReleaseVerificationError("Aura Sec platform evidence platform mismatch")
    digest = evidence.artifact_sha256.strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise PlatformReleaseVerificationError("Aura Sec platform evidence artifact SHA-256 is invalid")
    if not secrets.compare_digest(digest, manifest.artifact_sha256):
        raise PlatformReleaseVerificationError(
            "Aura Sec platform evidence is bound to a different artifact SHA-256"
        )
    if int(evidence.artifact_size_bytes) != int(manifest.artifact_size_bytes):
        raise PlatformReleaseVerificationError(
            "Aura Sec platform evidence is bound to a different artifact byte size"
        )
    signing_identity = evidence.signing_identity.strip()
    if not signing_identity or signing_identity not in identities:
        raise PlatformReleaseVerificationError(
            "Aura Sec platform signing identity is not in the trusted release allowlist"
        )
    if not evidence.verifier_id.strip() or not evidence.provider_request_id.strip():
        raise PlatformReleaseVerificationError(
            "Aura Sec platform verification evidence requires verifier and provider request identities"
        )
    verified_at = _utc(evidence.verified_at)
    if verified_at > current + _MAX_FUTURE_SKEW:
        raise PlatformReleaseVerificationError("Aura Sec platform verification evidence is from the future")
    if current - verified_at > _MAX_EVIDENCE_AGE:
        raise PlatformReleaseVerificationError("Aura Sec platform verification evidence is stale")
    if evidence.signature_valid is not True:
        raise PlatformReleaseVerificationError("Aura Sec native platform signature is not valid")

    if manifest.platform in {"windows", "macos"}:
        if evidence.trust_chain_valid is not True:
            raise PlatformReleaseVerificationError("Aura Sec native platform trust chain is not valid")
        if evidence.revocation_checked is not True:
            raise PlatformReleaseVerificationError("Aura Sec native platform revocation status was not checked")
        if evidence.timestamp_valid is not True:
            raise PlatformReleaseVerificationError("Aura Sec native platform signing timestamp is not valid")

    if manifest.platform == "macos" and evidence.notarization_valid is not True:
        raise PlatformReleaseVerificationError("Aura Sec macOS notarization evidence is not valid")

    if manifest.platform == "linux" and evidence.package_signature_valid is not True:
        raise PlatformReleaseVerificationError("Aura Sec Linux package signature evidence is not valid")

    return evidence


class AuraSecPlatformVerifiedReleaseAdmission:
    """Require platform authenticity evidence before durable Aura Sec release admission.

    The platform adapter runs first. Only if its evidence is current, artifact-bound,
    identity-allowlisted and policy-complete does the existing signed-release admission
    store verify the local artifact again and advance its anti-rollback high-water mark.
    This ordering prevents failed platform verification from advancing release state.

    The class does not download, install, execute or roll back software and does not expose
    browser routes. A real production deployment still needs concrete OS adapters and must
    preserve the admitted artifact identity through the native installation transaction.
    """

    def __init__(
        self,
        admission_store: AuraSecReleaseAdmissionStore,
        *,
        verifier: PlatformReleaseVerifier,
        expected_signing_identities: Sequence[str],
    ):
        if verifier is None:
            raise ValueError("Aura Sec platform release admission requires a native verifier adapter")
        identities = tuple(
            identity for identity in (str(item).strip() for item in expected_signing_identities) if identity
        )
        if not identities:
            raise ValueError("Aura Sec platform release admission requires trusted signing identities")
        self.admission_store = admission_store
        self.verifier = verifier
        self.expected_signing_identities = identities

    def admit_local_artifact(
        self,
        manifest: AuraSecReleaseManifest,
        artifact_path: str | Path,
        *,
        expected_channel: str,
        expected_platform: str,
        expected_architecture: str,
        now: datetime | None = None,
    ) -> PlatformVerifiedReleaseAdmissionDecision:
        current = _utc(now)
        if manifest.platform != expected_platform:
            raise PlatformReleaseVerificationError(
                "Aura Sec platform verifier target does not match the signed release platform"
            )
        path = Path(artifact_path)
        try:
            evidence = self.verifier.verify_release(path, manifest, now=current)
        except PlatformReleaseVerificationError:
            raise
        except Exception as exc:
            raise PlatformReleaseVerificationError(
                "Aura Sec native platform release verifier failed closed"
            ) from exc
        evidence = validate_platform_release_evidence(
            manifest,
            evidence,
            expected_signing_identities=self.expected_signing_identities,
            now=current,
        )
        release = self.admission_store.admit_local_artifact(
            manifest,
            path,
            expected_channel=expected_channel,
            expected_platform=expected_platform,
            expected_architecture=expected_architecture,
            now=current,
        )
        return PlatformVerifiedReleaseAdmissionDecision(
            release=release,
            platform_evidence=evidence,
        )


__all__ = [
    "AuraSecPlatformVerifiedReleaseAdmission",
    "PlatformReleaseEvidence",
    "PlatformReleaseVerificationError",
    "PlatformReleaseVerifier",
    "PlatformVerifiedReleaseAdmissionDecision",
    "validate_platform_release_evidence",
]
