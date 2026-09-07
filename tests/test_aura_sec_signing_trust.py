from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, SecurityCommand
from aura_music_studio.aura_sec_signing_trust import (
    AuraSecServerSigningTrustStore,
    TrustBoundServerCommandSigner,
)


DEVICE_ID = "device_rotation_001"
BASE = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)


def _signer(key_id: str) -> SelfHostedEd25519CommandSigner:
    return SelfHostedEd25519CommandSigner(Ed25519PrivateKey.generate(), key_id=key_id)


def _command(
    *,
    suffix: str = "001",
    device_id: str = DEVICE_ID,
    issued_at: datetime | None = None,
) -> SecurityCommand:
    issued = issued_at or (BASE + timedelta(seconds=1))
    return SecurityCommand(
        command_id=f"command_rotation_{suffix}",
        device_id=device_id,
        action=ActionType.REFRESH_SECURITY_STATE,
        risk=ActionRisk.READ_ONLY,
        issued_at=issued,
        expires_at=issued + timedelta(minutes=10),
        policy_version="policy-2026.09",
        nonce=f"rotation-nonce-{suffix}",
        parameters={},
    )


def test_bootstrap_establishes_one_active_public_trust_key_and_verifies_commands(tmp_path):
    signer = _signer("server-key-2026-09-a")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")

    active = store.bootstrap(
        signer.public_key_raw(),
        key_id=signer.key_id,
        now=BASE,
    )

    assert active.status == "active"
    assert active.key_id == signer.key_id
    assert active.public_key_fingerprint == signer.public_key_fingerprint
    assert store.current_generation() == 1
    assert set(store.trusted_public_keys(now=BASE)) == {signer.key_id}

    proof = store.verify_command(
        signer.sign_command(_command()),
        expected_device_id=DEVICE_ID,
        now=BASE + timedelta(seconds=2),
    )
    assert proof.signer_key_id == signer.key_id

    with pytest.raises(PermissionError, match="already been bootstrapped"):
        store.bootstrap(
            _signer("server-key-2026-09-b").public_key_raw(),
            key_id="server-key-2026-09-b",
            now=BASE + timedelta(minutes=1),
        )


def test_staged_successor_is_not_trusted_until_activation(tmp_path):
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)

    staged = store.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="planned quarterly signing key rotation",
        now=BASE + timedelta(minutes=1),
    )
    assert staged.status == "staged"
    assert store.current_generation() == 2
    assert set(store.trusted_public_keys(now=BASE + timedelta(minutes=2))) == {old.key_id}

    with pytest.raises(PermissionError, match="not trusted"):
        store.verify_command(
            new.sign_command(
                _command(
                    suffix="staged",
                    issued_at=BASE + timedelta(minutes=1, seconds=30),
                )
            ),
            expected_device_id=DEVICE_ID,
            now=BASE + timedelta(minutes=2),
        )


def test_activation_allows_bounded_overlap_then_old_key_fails_closed(tmp_path):
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)
    store.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="planned signing key rotation",
        now=BASE + timedelta(minutes=1),
    )

    active = store.activate_successor(
        new.key_id,
        overlap_seconds=600,
        reason="activate successor after fleet preparation",
        now=BASE + timedelta(minutes=2),
    )
    assert active.key_id == new.key_id
    assert active.status == "active"
    assert store.current_generation() == 3

    during = BASE + timedelta(minutes=5)
    assert set(store.trusted_public_keys(now=during)) == {old.key_id, new.key_id}
    during_issue = BASE + timedelta(minutes=4, seconds=30)
    store.verify_command(
        old.sign_command(_command(suffix="old-overlap", issued_at=during_issue)),
        expected_device_id=DEVICE_ID,
        now=during,
    )
    store.verify_command(
        new.sign_command(_command(suffix="new-active", issued_at=during_issue)),
        expected_device_id=DEVICE_ID,
        now=during,
    )

    after = BASE + timedelta(minutes=13)
    assert set(store.trusted_public_keys(now=after)) == {new.key_id}
    after_issue = BASE + timedelta(minutes=12, seconds=30)
    with pytest.raises(PermissionError, match="not trusted"):
        store.verify_command(
            old.sign_command(
                _command(suffix="old-after-overlap", issued_at=after_issue)
            ),
            expected_device_id=DEVICE_ID,
            now=after,
        )
    store.verify_command(
        new.sign_command(
            _command(suffix="new-after-overlap", issued_at=after_issue)
        ),
        expected_device_id=DEVICE_ID,
        now=after,
    )

    assert store.retire_expired(now=after) == [old.key_id]
    assert store.current_generation() == 4
    records = {item.key_id: item for item in store.keys()}
    assert records[old.key_id].status == "retired"
    assert records[old.key_id].retired_generation == 4
    assert records[new.key_id].status == "active"


def test_retired_key_identity_and_public_key_can_never_be_reintroduced(tmp_path):
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)
    store.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="rotate away from predecessor",
        now=BASE + timedelta(minutes=1),
    )
    store.activate_successor(
        new.key_id,
        overlap_seconds=300,
        reason="successor activation",
        now=BASE + timedelta(minutes=2),
    )
    store.retire_expired(now=BASE + timedelta(minutes=8))

    with pytest.raises(PermissionError, match="cannot reuse"):
        store.stage_successor(
            _signer(old.key_id).public_key_raw(),
            key_id=old.key_id,
            reason="attempted key id rollback",
            now=BASE + timedelta(minutes=9),
        )

    with pytest.raises(PermissionError, match="cannot reuse"):
        store.stage_successor(
            old.public_key_raw(),
            key_id="server-key-third-2026",
            reason="attempted public key rollback",
            now=BASE + timedelta(minutes=9),
        )


def test_rotation_disallows_multiple_simultaneous_transition_keys(tmp_path):
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")
    third = _signer("server-key-third-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)
    store.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="first planned successor",
        now=BASE + timedelta(minutes=1),
    )

    with pytest.raises(PermissionError, match="retire the prior transition"):
        store.stage_successor(
            third.public_key_raw(),
            key_id=third.key_id,
            reason="unsafe second staged successor",
            now=BASE + timedelta(minutes=2),
        )

    store.activate_successor(
        new.key_id,
        overlap_seconds=600,
        reason="activate first successor",
        now=BASE + timedelta(minutes=2),
    )
    with pytest.raises(PermissionError, match="retire the prior transition"):
        store.stage_successor(
            third.public_key_raw(),
            key_id=third.key_id,
            reason="unsafe rotation during overlap",
            now=BASE + timedelta(minutes=3),
        )


def test_overlap_window_is_bounded_and_cannot_be_zero_or_unbounded(tmp_path):
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)
    store.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="planned successor",
        now=BASE + timedelta(minutes=1),
    )

    for seconds in (0, 299, 14 * 24 * 60 * 60 + 1):
        with pytest.raises(ValueError, match="between 5 minutes and 14 days"):
            store.activate_successor(
                new.key_id,
                overlap_seconds=seconds,
                reason="invalid overlap attempt",
                now=BASE + timedelta(minutes=2),
            )


def test_trust_bound_signer_blocks_stale_signer_immediately_after_activation(tmp_path):
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)

    old_guarded = TrustBoundServerCommandSigner(store, old)
    signed = old_guarded.sign_command(_command(suffix="guarded-old"))
    assert signed.signer_key_id == old.key_id

    store.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="rotate guarded signer",
        now=BASE + timedelta(minutes=1),
    )
    store.activate_successor(
        new.key_id,
        overlap_seconds=600,
        reason="activate guarded successor",
        now=BASE + timedelta(minutes=2),
    )

    with pytest.raises(PermissionError, match="not the active trusted signing key"):
        old_guarded.sign_command(
            _command(
                suffix="stale-old",
                issued_at=BASE + timedelta(minutes=2, seconds=30),
            )
        )

    new_guarded = TrustBoundServerCommandSigner(store, new)
    fresh = new_guarded.sign_command(
        _command(
            suffix="guarded-new",
            issued_at=BASE + timedelta(minutes=2, seconds=30),
        )
    )
    assert fresh.signer_key_id == new.key_id


def test_trust_state_and_generation_survive_restart(tmp_path):
    path = tmp_path / "trust.sqlite3"
    old = _signer("server-key-old-2026")
    new = _signer("server-key-new-2026")

    first = AuraSecServerSigningTrustStore(path)
    first.bootstrap(old.public_key_raw(), key_id=old.key_id, now=BASE)
    first.stage_successor(
        new.public_key_raw(),
        key_id=new.key_id,
        reason="persistent rotation",
        now=BASE + timedelta(minutes=1),
    )
    first.activate_successor(
        new.key_id,
        overlap_seconds=600,
        reason="persistent successor activation",
        now=BASE + timedelta(minutes=2),
    )

    restarted = AuraSecServerSigningTrustStore(path)
    assert restarted.current_generation() == 3
    assert restarted.active_key().key_id == new.key_id
    assert set(restarted.trusted_public_keys(now=BASE + timedelta(minutes=5))) == {
        old.key_id,
        new.key_id,
    }
    assert [event.event_type for event in restarted.audit_events()] == [
        "bootstrap",
        "stage_successor",
        "activate_successor",
    ]


def test_device_targeting_still_fails_closed_through_rotation_store(tmp_path):
    signer = _signer("server-key-2026")
    store = AuraSecServerSigningTrustStore(tmp_path / "trust.sqlite3")
    store.bootstrap(signer.public_key_raw(), key_id=signer.key_id, now=BASE)
    signed = signer.sign_command(_command())

    with pytest.raises(PermissionError, match="different device"):
        store.verify_command(
            signed,
            expected_device_id="different_device_002",
            now=BASE + timedelta(minutes=1),
        )
