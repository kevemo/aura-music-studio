from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_release import AuraSecReleaseManifest, ReleaseManifestError
from aura_music_studio.aura_sec_release_trust import (
    AuraSecReleaseAdmissionStore,
    ReleaseAdmissionError,
)

NOW = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)
KEY_ID = "aura-sec-release-key-2026-09-a"


def _public_raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _artifact(tmp_path, name: str = "aura-sec.bin", content: bytes = b"aura-sec-release-artifact-v1"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _signed_manifest(
    private_key: Ed25519PrivateKey,
    artifact_path,
    *,
    key_id: str = KEY_ID,
    sequence: int = 42,
    version: str = "1.2.3",
    channel: str = "stable",
    platform: str = "windows",
    architecture: str = "x64",
    issued_at: datetime = NOW - timedelta(hours=1),
    expires_at: datetime = NOW + timedelta(days=1),
    artifact_sha256: str | None = None,
    artifact_size_bytes: int | None = None,
) -> AuraSecReleaseManifest:
    artifact = artifact_path.read_bytes()
    payload = {
        "schema_version": 2,
        "release_sequence": sequence,
        "product": "aura-sec",
        "channel": channel,
        "platform": platform,
        "architecture": architecture,
        "version": version,
        "minimum_os": "Windows 11",
        "artifact_url": f"https://downloads.example.test/aura-sec/{channel}/{platform}/{architecture}/{version}.bin",
        "artifact_sha256": artifact_sha256 or hashlib.sha256(artifact).hexdigest(),
        "artifact_size_bytes": artifact_size_bytes if artifact_size_bytes is not None else len(artifact),
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "signing_key_id": key_id,
        "signature_b64": base64.b64encode(b"0" * 64).decode("ascii"),
        "sbom_url": f"https://downloads.example.test/aura-sec/{channel}/{version}.sbom.spdx.json",
        "provenance_url": f"https://downloads.example.test/aura-sec/{channel}/{version}.provenance.json",
    }
    unsigned = AuraSecReleaseManifest.from_dict(payload)
    payload["signature_b64"] = base64.b64encode(private_key.sign(unsigned.signed_payload())).decode(
        "ascii"
    )
    return AuraSecReleaseManifest.from_dict(payload)


def _store(tmp_path, private_key: Ed25519PrivateKey, name: str = "release-trust.sqlite3"):
    return AuraSecReleaseAdmissionStore(
        tmp_path / name,
        trusted_release_public_keys={KEY_ID: _public_raw(private_key)},
    )


def _admit(store, manifest, artifact, **overrides):
    target = {
        "expected_channel": "stable",
        "expected_platform": "windows",
        "expected_architecture": "x64",
        "now": NOW,
    }
    target.update(overrides)
    return store.admit_local_artifact(manifest, artifact, **target)


def test_valid_signed_local_release_establishes_durable_high_water(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _signed_manifest(private, artifact)
    store = _store(tmp_path, private)

    decision = _admit(store, manifest, artifact)

    assert decision.accepted is True
    assert decision.replay is False
    assert decision.status == "accepted"
    assert decision.state.release_sequence == 42
    assert decision.state.version == "1.2.3"
    assert decision.state.artifact_sha256 == hashlib.sha256(artifact.read_bytes()).hexdigest()
    persisted = store.high_water(channel="stable", platform="windows", architecture="x64")
    assert persisted == decision.state
    assert [event.decision for event in store.audit_events()] == ["accepted"]


def test_exact_same_signed_release_is_idempotent_replay(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    manifest = _signed_manifest(private, artifact)
    store = _store(tmp_path, private)

    first = _admit(store, manifest, artifact)
    second = _admit(store, manifest, artifact)

    assert first.status == "accepted"
    assert second.status == "replay"
    assert second.replay is True
    assert second.state == first.state
    assert [event.decision for event in store.audit_events()] == ["accepted", "replay"]


def test_lower_release_sequence_is_rejected_as_rollback(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)
    _admit(store, _signed_manifest(private, artifact, sequence=42, version="2.0.0"), artifact)

    with pytest.raises(ReleaseAdmissionError, match="rollback rejected"):
        _admit(store, _signed_manifest(private, artifact, sequence=41, version="1.9.9"), artifact)

    assert store.high_water(channel="stable", platform="windows", architecture="x64").release_sequence == 42


def test_same_sequence_with_different_signed_metadata_is_rejected_as_equivocation(tmp_path):
    private = Ed25519PrivateKey.generate()
    first_artifact = _artifact(tmp_path, "first.bin", b"first immutable artifact")
    second_artifact = _artifact(tmp_path, "second.bin", b"different immutable artifact")
    store = _store(tmp_path, private)
    _admit(store, _signed_manifest(private, first_artifact, sequence=42), first_artifact)

    with pytest.raises(ReleaseAdmissionError, match="equivocation rejected"):
        _admit(store, _signed_manifest(private, second_artifact, sequence=42), second_artifact)


def test_higher_sequence_cannot_sneak_in_lower_semantic_version(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)
    _admit(store, _signed_manifest(private, artifact, sequence=42, version="2.1.0"), artifact)

    with pytest.raises(ReleaseAdmissionError, match="downgrade rejected"):
        _admit(store, _signed_manifest(private, artifact, sequence=43, version="2.0.9"), artifact)


def test_prerelease_to_final_semver_progression_is_allowed(tmp_path):
    private = Ed25519PrivateKey.generate()
    first_artifact = _artifact(tmp_path, "rc.bin", b"release candidate")
    final_artifact = _artifact(tmp_path, "final.bin", b"final release")
    store = _store(tmp_path, private)
    _admit(store, _signed_manifest(private, first_artifact, sequence=42, version="3.0.0-rc.1"), first_artifact)

    decision = _admit(
        store,
        _signed_manifest(private, final_artifact, sequence=43, version="3.0.0"),
        final_artifact,
    )
    assert decision.status == "accepted"
    assert decision.state.version == "3.0.0"


def test_invalid_non_semver_release_version_is_rejected(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)

    with pytest.raises(ReleaseAdmissionError, match="strict semantic versioning"):
        _admit(store, _signed_manifest(private, artifact, version="release_2026_09"), artifact)


def test_manifest_lifetime_is_bounded_to_seven_days_at_admission(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)
    manifest = _signed_manifest(
        private,
        artifact,
        issued_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=8),
    )

    with pytest.raises(ReleaseAdmissionError, match="expire within 7 days"):
        _admit(store, manifest, artifact)


def test_expired_and_not_yet_valid_manifests_fail_closed(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)

    expired = _signed_manifest(
        private,
        artifact,
        issued_at=NOW - timedelta(days=2),
        expires_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(ReleaseManifestError, match="expired"):
        _admit(store, expired, artifact)

    future = _signed_manifest(
        private,
        artifact,
        issued_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(days=1),
    )
    with pytest.raises(ReleaseManifestError, match="not valid yet"):
        _admit(store, future, artifact)


def test_untrusted_release_signer_is_rejected_even_with_valid_ed25519_signature(tmp_path):
    trusted = Ed25519PrivateKey.generate()
    untrusted = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, trusted)
    manifest = _signed_manifest(untrusted, artifact, key_id="aura-sec-release-key-2026-09-b")

    with pytest.raises(ReleaseManifestError, match="not trusted"):
        _admit(store, manifest, artifact)


def test_tampered_ed25519_signature_fails_closed(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)
    manifest = _signed_manifest(private, artifact)
    tampered = replace(manifest, signature_b64=base64.b64encode(b"x" * 64).decode("ascii"))

    with pytest.raises(ReleaseManifestError, match="signature is invalid"):
        _admit(store, tampered, artifact)


def test_local_artifact_digest_and_size_must_match_signed_metadata(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)

    bad_digest = _signed_manifest(private, artifact, artifact_sha256="f" * 64)
    with pytest.raises(ReleaseAdmissionError, match="SHA-256 does not match"):
        _admit(store, bad_digest, artifact)

    bad_size = _signed_manifest(private, artifact, artifact_size_bytes=len(artifact.read_bytes()) + 1)
    with pytest.raises(ReleaseAdmissionError, match="byte size does not match"):
        _admit(store, bad_size, artifact)


def test_updater_target_channel_platform_and_architecture_are_exact(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    store = _store(tmp_path, private)
    manifest = _signed_manifest(private, artifact)

    for field, kwargs in (
        ("channel", {"expected_channel": "beta"}),
        ("platform", {"expected_platform": "linux"}),
        ("architecture", {"expected_architecture": "arm64"}),
    ):
        with pytest.raises(ReleaseAdmissionError, match=f"release {field} mismatch"):
            _admit(store, manifest, artifact, **kwargs)


def test_high_water_is_separate_per_release_target(tmp_path):
    private = Ed25519PrivateKey.generate()
    stable_artifact = _artifact(tmp_path, "stable.bin", b"stable")
    beta_artifact = _artifact(tmp_path, "beta.bin", b"beta")
    store = _store(tmp_path, private)

    _admit(store, _signed_manifest(private, stable_artifact, sequence=50, version="5.0.0"), stable_artifact)
    beta = _signed_manifest(
        private,
        beta_artifact,
        sequence=2,
        version="1.0.0-beta.2",
        channel="beta",
    )
    decision = _admit(store, beta, beta_artifact, expected_channel="beta")

    assert decision.status == "accepted"
    assert store.high_water(channel="stable", platform="windows", architecture="x64").release_sequence == 50
    assert store.high_water(channel="beta", platform="windows", architecture="x64").release_sequence == 2


def test_high_water_and_audit_state_survive_restart(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path)
    db_path = tmp_path / "persistent-release-trust.sqlite3"
    first = AuraSecReleaseAdmissionStore(
        db_path,
        trusted_release_public_keys={KEY_ID: _public_raw(private)},
    )
    manifest = _signed_manifest(private, artifact, sequence=77, version="7.7.0")
    _admit(first, manifest, artifact)

    restarted = AuraSecReleaseAdmissionStore(
        db_path,
        trusted_release_public_keys={KEY_ID: _public_raw(private)},
    )
    persisted = restarted.high_water(channel="stable", platform="windows", architecture="x64")
    assert persisted is not None
    assert persisted.release_sequence == 77
    assert persisted.version == "7.7.0"
    replay = _admit(restarted, manifest, artifact)
    assert replay.status == "replay"
    assert [event.decision for event in restarted.audit_events()] == ["accepted", "replay"]


def test_release_trust_constructor_requires_explicit_release_public_keys(tmp_path):
    with pytest.raises(ValueError, match="requires at least one trusted release public key"):
        AuraSecReleaseAdmissionStore(tmp_path / "empty.sqlite3", trusted_release_public_keys={})


def test_symbolic_link_artifact_is_rejected_before_any_native_install_boundary(tmp_path):
    private = Ed25519PrivateKey.generate()
    artifact = _artifact(tmp_path, "real.bin", b"real artifact")
    link = tmp_path / "linked.bin"
    try:
        link.symlink_to(artifact)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links unavailable on this platform")
    store = _store(tmp_path, private)
    manifest = _signed_manifest(private, artifact)

    with pytest.raises(ReleaseAdmissionError, match="must not be a symbolic link"):
        _admit(store, manifest, link)
