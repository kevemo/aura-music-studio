from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_platform_release import (
    AuraSecPlatformVerifiedReleaseAdmission,
    PlatformReleaseEvidence,
    PlatformReleaseVerificationError,
    validate_platform_release_evidence,
)
from aura_music_studio.aura_sec_release import AuraSecReleaseManifest
from aura_music_studio.aura_sec_release_trust import AuraSecReleaseAdmissionStore

NOW = datetime(2026, 9, 1, 5, 45, tzinfo=timezone.utc)
RELEASE_KEY_ID = "aura-sec-release-key-2026-09-a"
WINDOWS_IDENTITY = "CN=Elevate Souls Productions AuraSec, O=Elevate Souls Productions"
MACOS_IDENTITY = "Developer ID Application: Elevate Souls Productions (TEAM123456)"
LINUX_IDENTITY = "AuraSec Linux Release Signing 2026"


def _public_raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _artifact(tmp_path, *, platform: str = "windows", content: bytes = b"native-aura-sec-release"):
    suffix = {"windows": ".exe", "macos": ".pkg", "linux": ".deb"}[platform]
    path = tmp_path / f"aura-sec{suffix}"
    path.write_bytes(content)
    return path


def _manifest(
    private_key: Ed25519PrivateKey,
    artifact_path,
    *,
    platform: str = "windows",
    architecture: str = "x64",
    sequence: int = 100,
    version: str = "4.2.0",
) -> AuraSecReleaseManifest:
    artifact = artifact_path.read_bytes()
    payload = {
        "schema_version": 2,
        "release_sequence": sequence,
        "product": "aura-sec",
        "channel": "stable",
        "platform": platform,
        "architecture": architecture,
        "version": version,
        "minimum_os": {
            "windows": "Windows 11",
            "macos": "macOS 15",
            "linux": "Ubuntu 24.04 LTS",
        }[platform],
        "artifact_url": f"https://downloads.example.test/aura-sec/stable/{platform}/{architecture}/{version}/aura-sec.bin",
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "artifact_size_bytes": len(artifact),
        "issued_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "signing_key_id": RELEASE_KEY_ID,
        "signature_b64": base64.b64encode(b"0" * 64).decode("ascii"),
        "sbom_url": f"https://downloads.example.test/aura-sec/stable/{platform}/{version}.sbom.spdx.json",
        "provenance_url": f"https://downloads.example.test/aura-sec/stable/{platform}/{version}.provenance.json",
    }
    unsigned = AuraSecReleaseManifest.from_dict(payload)
    payload["signature_b64"] = base64.b64encode(
        private_key.sign(unsigned.signed_payload())
    ).decode("ascii")
    return AuraSecReleaseManifest.from_dict(payload)


def _store(tmp_path, private_key: Ed25519PrivateKey) -> AuraSecReleaseAdmissionStore:
    return AuraSecReleaseAdmissionStore(
        tmp_path / "release-admission.sqlite3",
        trusted_release_public_keys={RELEASE_KEY_ID: _public_raw(private_key)},
    )


def _identity(platform: str) -> str:
    return {
        "windows": WINDOWS_IDENTITY,
        "macos": MACOS_IDENTITY,
        "linux": LINUX_IDENTITY,
    }[platform]


def _evidence(manifest: AuraSecReleaseManifest, **overrides) -> PlatformReleaseEvidence:
    data = {
        "platform": manifest.platform,
        "artifact_sha256": manifest.artifact_sha256,
        "artifact_size_bytes": manifest.artifact_size_bytes,
        "signing_identity": _identity(manifest.platform),
        "verifier_id": f"native-{manifest.platform}-signature-verifier-v1",
        "provider_request_id": f"verify-{manifest.release_sequence}-{manifest.platform}",
        "verified_at": NOW - timedelta(seconds=5),
        "signature_valid": True,
        "trust_chain_valid": True,
        "revocation_checked": True,
        "timestamp_valid": True,
        "notarization_valid": True if manifest.platform == "macos" else None,
        "package_signature_valid": True if manifest.platform == "linux" else None,
    }
    data.update(overrides)
    return PlatformReleaseEvidence(**data)


class FakeVerifier:
    def __init__(self, evidence: PlatformReleaseEvidence | None = None, error: Exception | None = None):
        self.evidence = evidence
        self.error = error
        self.calls = []

    def verify_release(self, artifact_path, manifest, *, now):
        self.calls.append((artifact_path, manifest.platform, now))
        if self.error is not None:
            raise self.error
        assert self.evidence is not None
        return self.evidence


def _wrapped(store, verifier, platform: str):
    return AuraSecPlatformVerifiedReleaseAdmission(
        store,
        verifier=verifier,
        expected_signing_identities=[_identity(platform)],
    )


def _admit(wrapper, manifest, artifact, *, platform: str | None = None, architecture: str | None = None):
    platform = platform or manifest.platform
    architecture = architecture or manifest.architecture
    return wrapper.admit_local_artifact(
        manifest,
        artifact,
        expected_channel="stable",
        expected_platform=platform,
        expected_architecture=architecture,
        now=NOW,
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_valid_platform_evidence_allows_existing_signed_release_admission(tmp_path, platform):
    private = Ed25519PrivateKey.generate()
    architecture = "arm64" if platform == "macos" else "x64"
    artifact = _artifact(tmp_path, platform=platform)
    manifest = _manifest(private, artifact, platform=platform, architecture=architecture)
    store = _store(tmp_path, private)
    verifier = FakeVerifier(_evidence(manifest))
    wrapper = _wrapped(store, verifier, platform)

    decision = _admit(wrapper, manifest, artifact)

    assert decision.accepted is True
    assert decision.release.status == "accepted"
    assert decision.platform_evidence.signing_identity == _identity(platform)
    assert verifier.calls == [(artifact, platform, NOW)]
    high_water = store.high_water(
        channel="stable", platform=platform, architecture=architecture
    )
    assert high_water is not None
    assert high_water.release_sequence == manifest.release_sequence


def test_platform_verifier_exception_fails_closed_without_advancing_high_water(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)
    store = _store(tmp_path, private)
    verifier = FakeVerifier(error=RuntimeError("native verifier unavailable"))
    wrapper = _wrapped(store, verifier, "windows")

    with pytest.raises(PlatformReleaseVerificationError, match="failed closed"):
        _admit(wrapper, manifest, artifact)

    assert store.high_water(channel="stable", platform="windows", architecture="x64") is None


def test_invalid_platform_evidence_fails_before_release_high_water_advances(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)
    store = _store(tmp_path, private)
    verifier = FakeVerifier(_evidence(manifest, signature_valid=False))
    wrapper = _wrapped(store, verifier, "windows")

    with pytest.raises(PlatformReleaseVerificationError, match="signature is not valid"):
        _admit(wrapper, manifest, artifact)

    assert store.high_water(channel="stable", platform="windows", architecture="x64") is None


def test_adapter_evidence_must_be_bound_to_exact_manifest_artifact(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)

    with pytest.raises(PlatformReleaseVerificationError, match="different artifact SHA-256"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, artifact_sha256="f" * 64),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )

    with pytest.raises(PlatformReleaseVerificationError, match="different artifact byte size"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, artifact_size_bytes=manifest.artifact_size_bytes + 1),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )


def test_platform_signing_identity_requires_exact_allowlist_match(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)

    with pytest.raises(PlatformReleaseVerificationError, match="not in the trusted release allowlist"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, signing_identity="CN=Unexpected Publisher"),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )


def test_platform_evidence_requires_verifier_and_provider_request_identity(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)

    for kwargs in ({"verifier_id": ""}, {"provider_request_id": ""}):
        with pytest.raises(PlatformReleaseVerificationError, match="requires verifier and provider request"):
            validate_platform_release_evidence(
                manifest,
                _evidence(manifest, **kwargs),
                expected_signing_identities=[WINDOWS_IDENTITY],
                now=NOW,
            )


def test_platform_evidence_is_short_lived_and_future_skew_is_bounded(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)

    with pytest.raises(PlatformReleaseVerificationError, match="stale"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, verified_at=NOW - timedelta(minutes=11)),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )

    with pytest.raises(PlatformReleaseVerificationError, match="from the future"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, verified_at=NOW + timedelta(seconds=31)),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )


@pytest.mark.parametrize(
    "field,message",
    [
        ("trust_chain_valid", "trust chain is not valid"),
        ("revocation_checked", "revocation status was not checked"),
        ("timestamp_valid", "signing timestamp is not valid"),
    ],
)
def test_windows_requires_chain_revocation_and_timestamp_evidence(tmp_path, field, message):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)

    with pytest.raises(PlatformReleaseVerificationError, match=message):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, **{field: False}),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )


def test_macos_requires_valid_notarization_in_addition_to_signature_chain(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path, platform="macos")
    manifest = _manifest(private, artifact, platform="macos", architecture="arm64")

    with pytest.raises(PlatformReleaseVerificationError, match="macOS notarization evidence is not valid"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, notarization_valid=False),
            expected_signing_identities=[MACOS_IDENTITY],
            now=NOW,
        )


def test_linux_requires_approved_package_signature_evidence(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path, platform="linux")
    manifest = _manifest(private, artifact, platform="linux")

    with pytest.raises(PlatformReleaseVerificationError, match="Linux package signature evidence is not valid"):
        validate_platform_release_evidence(
            manifest,
            _evidence(manifest, package_signature_valid=False),
            expected_signing_identities=[LINUX_IDENTITY],
            now=NOW,
        )


def test_non_native_release_platforms_are_not_silently_accepted_by_native_contract(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = tmp_path / "aura-sec-browser.zip"
    artifact.write_bytes(b"browser distribution")
    manifest = _manifest(private, artifact)
    browser_manifest = replace(manifest, platform="browser")

    with pytest.raises(PlatformReleaseVerificationError, match="only defined for windows, macos and linux"):
        validate_platform_release_evidence(
            browser_manifest,
            replace(_evidence(manifest), platform="browser"),
            expected_signing_identities=[WINDOWS_IDENTITY],
            now=NOW,
        )


def test_expected_platform_mismatch_rejects_before_native_verifier_call(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(private, artifact)
    store = _store(tmp_path, private)
    verifier = FakeVerifier(_evidence(manifest))
    wrapper = _wrapped(store, verifier, "windows")

    with pytest.raises(PlatformReleaseVerificationError, match="target does not match"):
        _admit(wrapper, manifest, artifact, platform="linux")

    assert verifier.calls == []
    assert store.high_water(channel="stable", platform="windows", architecture="x64") is None


def test_platform_wrapper_requires_real_adapter_and_trusted_signing_identity(tmp_path):
    private = Ed25519PrivateKey.generate()
    store = _store(tmp_path, private)

    with pytest.raises(ValueError, match="requires a native verifier adapter"):
        AuraSecPlatformVerifiedReleaseAdmission(
            store,
            verifier=None,
            expected_signing_identities=[WINDOWS_IDENTITY],
        )

    with pytest.raises(ValueError, match="requires trusted signing identities"):
        AuraSecPlatformVerifiedReleaseAdmission(
            store,
            verifier=FakeVerifier(),
            expected_signing_identities=[],
        )


def test_platform_evidence_does_not_override_existing_release_signature_or_artifact_checks(tmp_path):
    trusted_private = Ed25519PrivateKey.generate()
    attacker_private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _manifest(attacker_private, artifact)
    store = _store(tmp_path, trusted_private)
    verifier = FakeVerifier(_evidence(manifest))
    wrapper = _wrapped(store, verifier, "windows")

    with pytest.raises(Exception, match="signature"):
        _admit(wrapper, manifest, artifact)

    assert store.high_water(channel="stable", platform="windows", architecture="x64") is None
