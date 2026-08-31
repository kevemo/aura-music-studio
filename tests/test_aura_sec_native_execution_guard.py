from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_native_execution_guard import AuraSecNativeExecutionGuard
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, SecurityCommand


def _fixture(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    signer = SelfHostedEd25519CommandSigner(private_key, key_id="native-exec-test-key")
    device_id = "device-native-exec-0001"
    trusted = {signer.key_id: signer.public_key_raw()}
    guard = AuraSecNativeExecutionGuard(tmp_path / "native-execution.sqlite3")
    return guard, signer, trusted, device_id


def _signed(
    signer,
    device_id: str,
    *,
    command_id: str = "command-native-exec-0001",
    policy_version: str = "policy-1",
    nonce: str = "native-exec-nonce-00000001",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
):
    issued = issued_at or datetime.now(timezone.utc)
    expires = expires_at or issued + timedelta(minutes=5)
    return signer.sign_command(
        SecurityCommand(
            command_id=command_id,
            device_id=device_id,
            action=ActionType.RUN_QUICK_SCAN,
            risk=ActionRisk.LOW_RISK,
            issued_at=issued,
            expires_at=expires,
            policy_version=policy_version,
            nonce=nonce,
            parameters={},
        )
    )


def test_first_verified_command_is_durably_reserved_once(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)

    reservation = guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )

    assert reservation.execute is True
    assert reservation.duplicate is False
    assert reservation.state == "reserved"
    stored = guard.get(device_id=device_id, command_id=command.command_id)
    assert stored["state"] == "reserved"
    assert stored["action_type"] == ActionType.RUN_QUICK_SCAN.value
    assert stored["server_payload_digest"] == command.payload_digest
    assert "signature" not in stored


def test_exact_redelivery_is_verified_again_but_never_executes_twice(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    first = guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    second = guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )

    assert first.execute is True
    assert second.execute is False
    assert second.duplicate is True
    assert second.state == "reserved"
    assert second.first_reserved_at == first.first_reserved_at


def test_duplicate_path_still_fails_closed_on_invalid_signature(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )

    tampered = command.model_copy(
        update={"signature_b64": base64.b64encode(b"x" * 64).decode("ascii")}
    )
    with pytest.raises(PermissionError, match="signature verification failed"):
        guard.reserve_verified_command(
            tampered,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
        )


def test_valid_resigned_command_cannot_rebind_an_existing_command_id(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    original = _signed(signer, device_id)
    guard.reserve_verified_command(
        original,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )

    rebound = _signed(
        signer,
        device_id,
        command_id=original.command_id,
        policy_version="policy-2",
        nonce="native-exec-nonce-00000002",
    )
    with pytest.raises(PermissionError, match="command id was rebound"):
        guard.reserve_verified_command(
            rebound,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
        )


def test_completion_is_idempotent_and_blocks_future_execution(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    evidence = "a" * 64
    completed = guard.mark_completed(
        device_id=device_id,
        command_id=command.command_id,
        result_evidence_digest=evidence,
    )
    repeated_completion = guard.mark_completed(
        device_id=device_id,
        command_id=command.command_id,
        result_evidence_digest=evidence,
    )
    redelivery = guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )

    assert completed["state"] == "completed"
    assert repeated_completion["state"] == "completed"
    assert redelivery.execute is False
    assert redelivery.duplicate is True
    assert redelivery.state == "completed"


def test_conflicting_completion_evidence_is_rejected(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    guard.mark_completed(
        device_id=device_id,
        command_id=command.command_id,
        result_evidence_digest="a" * 64,
    )

    with pytest.raises(ValueError, match="Conflicting Aura Sec completion evidence"):
        guard.mark_completed(
            device_id=device_id,
            command_id=command.command_id,
            result_evidence_digest="b" * 64,
        )


def test_failed_command_is_not_automatically_retried(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    failed = guard.mark_failed(
        device_id=device_id,
        command_id=command.command_id,
        failure_code="platform.executor_failed",
    )
    redelivery = guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )

    assert failed["state"] == "failed"
    assert redelivery.execute is False
    assert redelivery.duplicate is True
    assert redelivery.state == "failed"


def test_crash_ambiguity_survives_guard_restart_and_suppresses_replay(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    first = guard.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    assert first.execute is True

    restarted = AuraSecNativeExecutionGuard(tmp_path / "native-execution.sqlite3")
    after_restart = restarted.reserve_verified_command(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    assert after_restart.execute is False
    assert after_restart.duplicate is True
    assert after_restart.state == "reserved"


def test_wrong_device_or_expired_command_fails_before_reservation(tmp_path):
    guard, signer, trusted, device_id = _fixture(tmp_path)
    command = _signed(signer, device_id)
    with pytest.raises(PermissionError, match="different device"):
        guard.reserve_verified_command(
            command,
            trusted_public_keys=trusted,
            expected_device_id="device-native-exec-9999",
        )

    now = datetime.now(timezone.utc)
    expired = _signed(
        signer,
        device_id,
        command_id="command-native-exec-expired",
        nonce="native-exec-nonce-expired-001",
        issued_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
    )
    with pytest.raises(PermissionError, match="has expired"):
        guard.reserve_verified_command(
            expired,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            now=now,
        )
    with pytest.raises(ValueError, match="record not found"):
        guard.get(device_id=device_id, command_id=expired.command_id)
