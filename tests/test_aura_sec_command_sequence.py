from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_command_sequence import (
    AuraSecNativeCommandSequenceGuard,
    AuraSecSequencedNativeExecutionGate,
    command_sequence_from_nonce,
    sequenced_command_nonce,
)
from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_native_bridge import (
    AuraSecNativeBridge,
    NativeCommandPoll,
    VerifiedNativePollSignature,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, SecurityCommand
from aura_music_studio.aura_sec_store import AuraSecStore


_POLL_SECRET = b"aura-sec-command-sequence-poll-secret-v1"


def _signer():
    return SelfHostedEd25519CommandSigner(
        Ed25519PrivateKey.generate(),
        key_id="sequence-test-server-key",
    )


def _signed_command(
    signer,
    device_id: str,
    *,
    sequence: int,
    command_id: str,
    entropy: str,
):
    now = datetime.now(timezone.utc)
    command = SecurityCommand(
        command_id=command_id,
        device_id=device_id,
        action=ActionType.RUN_QUICK_SCAN,
        risk=ActionRisk.LOW_RISK,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        policy_version="policy-sequence-1",
        nonce=sequenced_command_nonce(sequence, entropy=entropy),
        parameters={},
    )
    return signer.sign_command(command)


def test_sequenced_nonce_round_trips_and_rejects_unstructured_values():
    nonce = sequenced_command_nonce(42, entropy="a" * 32)
    assert nonce == f"aseq1.42.{'a' * 32}"
    assert command_sequence_from_nonce(nonce) == 42

    with pytest.raises(ValueError, match="outside the supported range"):
        sequenced_command_nonce(0, entropy="a" * 32)
    with pytest.raises(TypeError, match="must be an integer"):
        sequenced_command_nonce(True, entropy="a" * 32)
    with pytest.raises(PermissionError, match="missing a valid anti-rollback sequence"):
        command_sequence_from_nonce("ordinary-random-nonce-without-sequence")


def test_native_sequence_guard_accepts_newer_and_exact_retry_but_rejects_rollback(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-sequence-guard-0001"
    guard = AuraSecNativeCommandSequenceGuard(tmp_path / "sequence.sqlite3")

    ten = _signed_command(
        signer,
        device_id,
        sequence=10,
        command_id="command-sequence-0010",
        entropy="a" * 32,
    )
    first = guard.accept_verified_command(
        ten,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    retry = guard.accept_verified_command(
        ten,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    assert first.sequence == 10
    assert first.duplicate is False
    assert retry.sequence == 10
    assert retry.duplicate is True

    eleven = _signed_command(
        signer,
        device_id,
        sequence=11,
        command_id="command-sequence-0011",
        entropy="b" * 32,
    )
    newer = guard.accept_verified_command(
        eleven,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    assert newer.sequence == 11
    assert newer.duplicate is False

    with pytest.raises(PermissionError, match="moved backwards"):
        guard.accept_verified_command(
            ten,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
        )
    state = guard.state(device_id)
    assert state is not None
    assert state["last_sequence"] == 11
    assert state["last_command_id"] == "command-sequence-0011"


def test_same_sequence_cannot_be_reused_for_different_valid_signed_content(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-sequence-guard-0002"
    guard = AuraSecNativeCommandSequenceGuard(tmp_path / "sequence-reuse.sqlite3")

    original = _signed_command(
        signer,
        device_id,
        sequence=25,
        command_id="command-sequence-0025a",
        entropy="c" * 32,
    )
    rebound = _signed_command(
        signer,
        device_id,
        sequence=25,
        command_id="command-sequence-0025b",
        entropy="d" * 32,
    )
    guard.accept_verified_command(
        original,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    with pytest.raises(PermissionError, match="reused for different content"):
        guard.accept_verified_command(
            rebound,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
        )


def test_sequence_tampering_fails_server_signature_before_state_advances(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-sequence-guard-0003"
    guard = AuraSecNativeCommandSequenceGuard(tmp_path / "sequence-tamper.sqlite3")

    signed = _signed_command(
        signer,
        device_id,
        sequence=7,
        command_id="command-sequence-0007",
        entropy="e" * 32,
    )
    tampered = signed.model_copy(
        update={"nonce": sequenced_command_nonce(8, entropy="e" * 32)}
    )
    with pytest.raises(PermissionError, match="digest|signature"):
        guard.accept_verified_command(
            tampered,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
        )
    assert guard.state(device_id) is None


def test_sequenced_execution_gate_blocks_old_valid_command_after_newer_acceptance(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-sequence-gate-0001"
    gate = AuraSecSequencedNativeExecutionGate(tmp_path / "sequence-gate.sqlite3")

    ten = _signed_command(
        signer,
        device_id,
        sequence=10,
        command_id="command-gate-0010",
        entropy="f" * 32,
    )
    eleven = _signed_command(
        signer,
        device_id,
        sequence=11,
        command_id="command-gate-0011",
        entropy="g" * 32,
    )

    first = gate.reserve_verified_command(
        ten,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    second = gate.reserve_verified_command(
        eleven,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
    )
    assert first.execution.execute is True
    assert second.execution.execute is True

    with pytest.raises(PermissionError, match="moved backwards"):
        gate.reserve_verified_command(
            ten,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
        )


def _bridge_setup(tmp_path):
    accounts = AccountStore(tmp_path / "bridge-sequence.sqlite3")
    signup = accounts.signup(
        "sequence.bridge@example.test",
        "Sequence Bridge Member",
        "secure-sequence-bridge-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="sequence-bridge-payment",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Sequence Test Device",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="9" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
        details={"command_parameters": {"object_id": "sequence-object-1"}},
    )
    security.approve_action(signup.user_id, action["id"])
    signer = _signer()
    bridge = AuraSecNativeBridge(
        accounts,
        security,
        AuraSecCommandStore(accounts, security),
        command_signer=signer,
    )
    return signup.user_id, device, signer, bridge


def _poll(device_id: str, sequence: int) -> NativeCommandPoll:
    now = datetime.now(timezone.utc)
    return NativeCommandPoll(
        device_id=device_id,
        sequence=sequence,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        agent_version="0.1.0",
        policy_version="policy-sequence-1",
        session_nonce=f"sequence-poll-nonce-{sequence:08d}",
    )


def _poll_signature(poll: NativeCommandPoll) -> str:
    raw = hashlib.sha256(_POLL_SECRET + poll.signed_payload()).digest()
    return base64.b64encode(raw).decode("ascii")


def _poll_verifier(fingerprint: str, payload: bytes, signature: bytes):
    expected = hashlib.sha256(_POLL_SECRET + payload).digest()
    if not secrets.compare_digest(signature, expected):
        return None
    return VerifiedNativePollSignature(
        public_key_fingerprint=fingerprint,
        verifier_id="sequence-poll-verifier",
        key_algorithm="p256",
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


def test_native_bridge_binds_verified_poll_sequence_into_server_signed_command(tmp_path):
    user_id, device, signer, bridge = _bridge_setup(tmp_path)
    poll = _poll(device["id"], 37)
    result = bridge.poll_verified_command(
        user_id,
        poll,
        signature_b64=_poll_signature(poll),
        signature_verifier=_poll_verifier,
    )
    command = result["command"]
    assert command is not None
    assert command_sequence_from_nonce(command["nonce"]) == 37
    assert result["poll_sequence"] == 37
    assert command["signer_key_id"] == signer.key_id
