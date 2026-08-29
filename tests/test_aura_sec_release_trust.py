from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.aura_sec_release_trust import (
    AuraSecReleaseArtifact,
    AuraSecReleaseManifest,
    VerifiedReleaseManifest,
    canonical_release_manifest_payload,
    verify_release_manifest_contract,
)


def _manifest(*, signer_id="release-signer-1", expires_delta=timedelta(hours=1)):
    now = datetime.now(timezone.utc)
    return AuraSecReleaseManifest(
        release_id="release-2026-08-29-001",
        channel="stable",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + expires_delta,
        signer_id=signer_id,
        provenance_uri="https://releases.example.test/provenance/001.json",
        sbom_uri="https://releases.example.test/sbom/001.spdx.json",
        artifacts=(
            AuraSecReleaseArtifact(
                platform="windows",
                architecture="x64",
                version="1.0.0",
                download_url="https://releases.example.test/aura-sec/windows-x64.exe",
                sha256="a" * 64,
                size_bytes=123456,
            ),
        ),
    )


def _verification(manifest, *, signer_id=None, digest=None, algorithm="ed25519"):
    payload = canonical_release_manifest_payload(manifest)
    return VerifiedReleaseManifest(
        signer_id=signer_id or manifest.signer_id,
        verifier_id="test-release-verifier",
        manifest_digest=digest or hashlib.sha256(payload).hexdigest(),
        signature_algorithm=algorithm,
    )


def test_verified_release_requires_trusted_signer_and_exact_manifest_digest():
    manifest = _manifest()
    digest = verify_release_manifest_contract(
        manifest,
        _verification(manifest),
        trusted_signers={"release-signer-1"},
        now=datetime.now(timezone.utc),
    )
    assert digest == hashlib.sha256(canonical_release_manifest_payload(manifest)).hexdigest()


def test_untrusted_or_mismatched_signer_fails_closed():
    manifest = _manifest()
    with pytest.raises(PermissionError, match="not trusted"):
        verify_release_manifest_contract(
            manifest,
            _verification(manifest),
            trusted_signers={"different-signer"},
            now=datetime.now(timezone.utc),
        )
    with pytest.raises(PermissionError, match="does not match"):
        verify_release_manifest_contract(
            manifest,
            _verification(manifest, signer_id="different-signer"),
            trusted_signers={"release-signer-1"},
            now=datetime.now(timezone.utc),
        )


def test_modified_manifest_digest_is_rejected():
    manifest = _manifest()
    with pytest.raises(PermissionError, match="exact manifest"):
        verify_release_manifest_contract(
            manifest,
            _verification(manifest, digest="b" * 64),
            trusted_signers={"release-signer-1"},
            now=datetime.now(timezone.utc),
        )


def test_expired_manifest_is_rejected():
    manifest = _manifest(expires_delta=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="expiry must follow issuance"):
        canonical_release_manifest_payload(manifest)


def test_insecure_download_or_missing_sbom_provenance_is_rejected():
    manifest = _manifest()
    insecure_artifact = AuraSecReleaseArtifact(
        platform="windows",
        architecture="x64",
        version="1.0.0",
        download_url="http://releases.example.test/aura-sec.exe",
        sha256="a" * 64,
        size_bytes=1,
    )
    bad = AuraSecReleaseManifest(
        release_id=manifest.release_id,
        channel=manifest.channel,
        issued_at=manifest.issued_at,
        expires_at=manifest.expires_at,
        signer_id=manifest.signer_id,
        provenance_uri=manifest.provenance_uri,
        sbom_uri=manifest.sbom_uri,
        artifacts=(insecure_artifact,),
    )
    with pytest.raises(ValueError, match="HTTPS downloads"):
        canonical_release_manifest_payload(bad)


def test_duplicate_platform_target_is_rejected():
    manifest = _manifest()
    first = manifest.artifacts[0]
    duplicate = AuraSecReleaseArtifact(
        platform="windows",
        architecture="x64",
        version="1.0.1",
        download_url="https://releases.example.test/aura-sec/windows-x64-v2.exe",
        sha256="b" * 64,
        size_bytes=2,
    )
    bad = AuraSecReleaseManifest(
        release_id=manifest.release_id,
        channel=manifest.channel,
        issued_at=manifest.issued_at,
        expires_at=manifest.expires_at,
        signer_id=manifest.signer_id,
        provenance_uri=manifest.provenance_uri,
        sbom_uri=manifest.sbom_uri,
        artifacts=(first, duplicate),
    )
    with pytest.raises(ValueError, match="duplicate platform target"):
        canonical_release_manifest_payload(bad)
