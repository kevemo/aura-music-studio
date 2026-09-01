from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_release_key_trust import AuraSecReleaseSigningTrustStore
from aura_music_studio.aura_sec_release_trust import AuraSecReleaseAdmissionStore

BASE = datetime(2026, 9, 1, 5, 30, tzinfo=timezone.utc)
OLD_ID = "aura-sec-release-key-2026-09-a"
NEW_ID = "aura-sec-release-key-2026-09-b"


def _private() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def test_bootstrap_establishes_exactly_one_active_release_public_key(tmp_path):
    old = _private()
    store = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")

    active = store.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)

    assert active.status == "active"
    assert active.key_id == OLD_ID
    assert active.public_key_raw == _raw(old)
    assert store.current_generation() == 1
    assert dict(store.trusted_public_keys(now=BASE)) == {OLD_ID: _raw(old)}
    assert [event.event_type for event in store.audit_events()] == ["bootstrap"]

    with pytest.raises(PermissionError, match="already been bootstrapped"):
        store.bootstrap(_raw(_private()), key_id=NEW_ID, now=BASE + timedelta(minutes=1))


def test_staged_successor_is_not_trusted_for_release_verification(tmp_path):
    old = _private()
    new = _private()
    store = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")
    store.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)

    staged = store.stage_successor(
        _raw(new),
        key_id=NEW_ID,
        reason="planned release signing key rotation",
        now=BASE + timedelta(minutes=1),
    )

    assert staged.status == "staged"
    assert store.current_generation() == 2
    assert dict(store.trusted_public_keys(now=BASE + timedelta(minutes=2))) == {OLD_ID: _raw(old)}


def test_activation_trusts_successor_and_predecessor_only_during_bounded_overlap(tmp_path):
    old = _private()
    new = _private()
    store = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")
    store.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)
    store.stage_successor(
        _raw(new),
        key_id=NEW_ID,
        reason="stage release successor",
        now=BASE + timedelta(minutes=1),
    )
    active = store.activate_successor(
        NEW_ID,
        overlap_seconds=600,
        reason="activate release successor after fleet preparation",
        now=BASE + timedelta(minutes=2),
    )

    assert active.status == "active"
    assert active.key_id == NEW_ID
    assert store.current_generation() == 3
    during = dict(store.trusted_public_keys(now=BASE + timedelta(minutes=5)))
    assert during == {NEW_ID: _raw(new), OLD_ID: _raw(old)}

    after = dict(store.trusted_public_keys(now=BASE + timedelta(minutes=13)))
    assert after == {NEW_ID: _raw(new)}
    old_record = {item.key_id: item for item in store.keys()}[OLD_ID]
    assert old_record.status == "overlap"
    assert old_record.overlap_until == BASE + timedelta(minutes=12)


def test_expired_predecessor_can_be_durably_retired_and_never_reintroduced(tmp_path):
    old = _private()
    new = _private()
    store = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")
    store.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)
    store.stage_successor(
        _raw(new),
        key_id=NEW_ID,
        reason="planned release rotation",
        now=BASE + timedelta(minutes=1),
    )
    store.activate_successor(
        NEW_ID,
        overlap_seconds=300,
        reason="activate release successor",
        now=BASE + timedelta(minutes=2),
    )

    assert store.retire_expired(now=BASE + timedelta(minutes=8)) == [OLD_ID]
    assert store.current_generation() == 4
    records = {item.key_id: item for item in store.keys()}
    assert records[OLD_ID].status == "retired"
    assert records[OLD_ID].retired_generation == 4
    assert records[NEW_ID].status == "active"

    with pytest.raises(PermissionError, match="cannot reuse"):
        store.stage_successor(
            _raw(_private()),
            key_id=OLD_ID,
            reason="attempt key id rollback",
            now=BASE + timedelta(minutes=9),
        )

    with pytest.raises(PermissionError, match="cannot reuse"):
        store.stage_successor(
            _raw(old),
            key_id="aura-sec-release-key-2026-09-c",
            reason="attempt public key rollback",
            now=BASE + timedelta(minutes=9),
        )


def test_rotation_disallows_multiple_simultaneous_release_key_transitions(tmp_path):
    old = _private()
    new = _private()
    third = _private()
    third_id = "aura-sec-release-key-2026-09-c"
    store = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")
    store.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)
    store.stage_successor(
        _raw(new),
        key_id=NEW_ID,
        reason="first release successor",
        now=BASE + timedelta(minutes=1),
    )

    with pytest.raises(PermissionError, match="retire the prior transition"):
        store.stage_successor(
            _raw(third),
            key_id=third_id,
            reason="unsafe second successor",
            now=BASE + timedelta(minutes=2),
        )

    store.activate_successor(
        NEW_ID,
        overlap_seconds=600,
        reason="activate first successor",
        now=BASE + timedelta(minutes=2),
    )
    with pytest.raises(PermissionError, match="retire the prior transition"):
        store.stage_successor(
            _raw(third),
            key_id=third_id,
            reason="unsafe successor during overlap",
            now=BASE + timedelta(minutes=3),
        )


def test_release_key_overlap_window_is_bounded(tmp_path):
    old = _private()
    new = _private()
    store = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")
    store.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)
    store.stage_successor(
        _raw(new),
        key_id=NEW_ID,
        reason="planned successor",
        now=BASE + timedelta(minutes=1),
    )

    for seconds in (0, 299, 14 * 24 * 60 * 60 + 1):
        with pytest.raises(ValueError, match="between 5 minutes and 14 days"):
            store.activate_successor(
                NEW_ID,
                overlap_seconds=seconds,
                reason="invalid release overlap",
                now=BASE + timedelta(minutes=2),
            )


def test_release_key_trust_generation_and_audit_survive_restart(tmp_path):
    path = tmp_path / "release-key-trust.sqlite3"
    old = _private()
    new = _private()
    first = AuraSecReleaseSigningTrustStore(path)
    first.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)
    first.stage_successor(
        _raw(new),
        key_id=NEW_ID,
        reason="persistent release successor",
        now=BASE + timedelta(minutes=1),
    )
    first.activate_successor(
        NEW_ID,
        overlap_seconds=600,
        reason="persistent release activation",
        now=BASE + timedelta(minutes=2),
    )

    restarted = AuraSecReleaseSigningTrustStore(path)
    assert restarted.current_generation() == 3
    assert restarted.active_key().key_id == NEW_ID
    assert set(restarted.trusted_public_keys(now=BASE + timedelta(minutes=5))) == {OLD_ID, NEW_ID}
    assert [event.event_type for event in restarted.audit_events()] == [
        "bootstrap",
        "stage_successor",
        "activate_successor",
    ]


def test_snapshot_is_immutable_and_can_feed_release_admission_without_command_keys(tmp_path):
    old = _private()
    trust = AuraSecReleaseSigningTrustStore(tmp_path / "shared-release-security.sqlite3")
    trust.bootstrap(_raw(old), key_id=OLD_ID, now=BASE)

    snapshot = trust.snapshot(now=BASE + timedelta(minutes=1))
    assert snapshot.generation == 1
    assert dict(snapshot.public_keys) == {OLD_ID: _raw(old)}
    with pytest.raises(TypeError):
        snapshot.public_keys["forged-key"] = b"x" * 32

    admission = AuraSecReleaseAdmissionStore(
        tmp_path / "release-admission.sqlite3",
        trusted_release_public_keys=snapshot.public_keys,
    )
    assert admission.high_water(channel="stable", platform="windows", architecture="x64") is None


def test_release_and_server_command_key_domains_are_not_implicitly_shared(tmp_path):
    release = _private()
    command = _private()
    trust = AuraSecReleaseSigningTrustStore(tmp_path / "release-key-trust.sqlite3")
    trust.bootstrap(_raw(release), key_id=OLD_ID, now=BASE)

    snapshot = dict(trust.trusted_public_keys(now=BASE))
    assert snapshot == {OLD_ID: _raw(release)}
    assert _raw(command) not in snapshot.values()
