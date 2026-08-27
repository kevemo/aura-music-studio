from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from aura_music_studio.aura_sec_release import (
    AuraSecReleaseManifest,
    ReleaseManifestError,
    validate_release_manifest,
)


NOW = datetime(2026, 8, 27, 2, 45, tzinfo=timezone.utc)
TRUSTED_KEY = "aura-sec-release-key-2026-01"


def _payload(**overrides):
    data = {
        "schema_version": 1,
        "product": "aura-sec",
        "channel": "stable",
        "platform": "windows",
        "architecture": "x64",
        "version": "0.1.0",
        "minimum_os": "Windows 11",
        "artifact_url": "https://downloads.example.test/aura-sec/windows/x64/aura-sec-0.1.0.exe",
        "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 50_000_000,
        "issued_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-09-03T00:00:00Z",
        "signing_key_id": TRUSTED_KEY,
        "signature_b64": base64.b64encode(b"s" * 64).decode("ascii"),
        "sbom_url": "https://downloads.example.test/aura-sec/windows/x64/aura-sec-0.1.0.sbom.spdx.json",
        "provenance_url": "https://downloads.example.test/aura-sec/windows/x64/aura-sec-0.1.0.provenance.json",
    }
    data.update(overrides)
    return data


def _manifest(**overrides):
    return AuraSecReleaseManifest.from_dict(_payload(**overrides))


def test_release_requires_real_signature_verifier():
    with pytest.raises(ReleaseManifestError, match="No Aura Sec release signature verifier"):
        validate_release_manifest(
            _manifest(),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=None,
            now=NOW,
        )


def test_verified_manifest_is_downloadable_only_after_signature_callback_succeeds():
    calls = []

    def verifier(key_id: str, payload: bytes, signature: bytes) -> bool:
        calls.append((key_id, payload, signature))
        return key_id == TRUSTED_KEY and payload.startswith(b"AURA-SEC-RELEASE-V1\n") and len(signature) == 64

    result = validate_release_manifest(
        _manifest(),
        trusted_key_ids={TRUSTED_KEY},
        signature_verifier=verifier,
        now=NOW,
    )
    assert result["verified"] is True
    assert result["downloadable"] is True
    assert result["artifact_sha256"] == "a" * 64
    assert calls


def test_bad_signature_fails_closed():
    with pytest.raises(ReleaseManifestError, match="signature is invalid"):
        validate_release_manifest(
            _manifest(),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=lambda *_args: False,
            now=NOW,
        )


def test_untrusted_signing_identity_is_rejected_before_download():
    with pytest.raises(ReleaseManifestError, match="not trusted"):
        validate_release_manifest(
            _manifest(),
            trusted_key_ids={"another-release-key"},
            signature_verifier=lambda *_args: True,
            now=NOW,
        )


def test_artifact_and_evidence_urls_must_be_https():
    with pytest.raises(ReleaseManifestError, match="artifact_url must be an absolute HTTPS URL"):
        validate_release_manifest(
            _manifest(artifact_url="http://downloads.example.test/aura.exe"),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=lambda *_args: True,
            now=NOW,
        )


def test_expired_manifest_is_rejected():
    with pytest.raises(ReleaseManifestError, match="expired"):
        validate_release_manifest(
            _manifest(expires_at="2026-08-27T01:00:00Z"),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=lambda *_args: True,
            now=NOW,
        )


def test_manifest_schema_rejects_unknown_fields():
    payload = _payload(extra_download_override="unsafe")
    with pytest.raises(ReleaseManifestError, match="Unexpected release manifest fields"):
        AuraSecReleaseManifest.from_dict(payload)


def test_manifest_hash_has_fixed_sha256_shape():
    with pytest.raises(ReleaseManifestError, match="SHA-256"):
        validate_release_manifest(
            _manifest(artifact_sha256="abc"),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=lambda *_args: True,
            now=NOW,
        )
