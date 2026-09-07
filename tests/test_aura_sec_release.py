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
        "schema_version": 2,
        "release_sequence": 42,
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


def _valid_verifier(key_id: str, payload: bytes, signature: bytes) -> bool:
    return key_id == TRUSTED_KEY and payload.startswith(b"AURA-SEC-RELEASE-V2\n") and len(signature) == 64


def test_release_requires_real_signature_verifier():
    with pytest.raises(ReleaseManifestError, match="No Aura Sec release signature verifier"):
        validate_release_manifest(_manifest(), trusted_key_ids={TRUSTED_KEY}, signature_verifier=None, now=NOW)


def test_verified_manifest_is_downloadable_only_after_signature_callback_succeeds():
    calls = []

    def verifier(key_id: str, payload: bytes, signature: bytes) -> bool:
        calls.append((key_id, payload, signature))
        return _valid_verifier(key_id, payload, signature)

    result = validate_release_manifest(
        _manifest(),
        trusted_key_ids={TRUSTED_KEY},
        signature_verifier=verifier,
        now=NOW,
    )
    assert result["verified"] is True
    assert result["downloadable"] is True
    assert result["release_sequence"] == 42
    assert calls and b"\n42\n" in calls[0][1]


def test_legacy_manifest_schema_without_signed_sequence_is_rejected():
    with pytest.raises(ReleaseManifestError, match="signed sequence metadata is required"):
        validate_release_manifest(
            _manifest(schema_version=1),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=_valid_verifier,
            now=NOW,
        )


def test_valid_old_signed_release_is_rejected_as_rollback():
    with pytest.raises(ReleaseManifestError, match="rollback rejected"):
        validate_release_manifest(
            _manifest(release_sequence=41),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=_valid_verifier,
            now=NOW,
            minimum_release_sequence=42,
            trusted_sequence_sha256="a" * 64,
        )


def test_same_sequence_can_only_redownload_identical_trusted_artifact():
    result = validate_release_manifest(
        _manifest(release_sequence=42),
        trusted_key_ids={TRUSTED_KEY},
        signature_verifier=_valid_verifier,
        now=NOW,
        minimum_release_sequence=42,
        trusted_sequence_sha256="a" * 64,
    )
    assert result["downloadable"] is True
    with pytest.raises(ReleaseManifestError, match="equivocation rejected"):
        validate_release_manifest(
            _manifest(release_sequence=42, artifact_sha256="b" * 64),
            trusted_key_ids={TRUSTED_KEY},
            signature_verifier=_valid_verifier,
            now=NOW,
            minimum_release_sequence=42,
            trusted_sequence_sha256="a" * 64,
        )


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


def test_manifest_schema_rejects_unknown_fields():
    with pytest.raises(ReleaseManifestError, match="Unexpected release manifest fields"):
        AuraSecReleaseManifest.from_dict(_payload(extra_download_override="unsafe"))
