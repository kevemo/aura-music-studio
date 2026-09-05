from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_bound_attestation import (
    AuraSecCommandBoundAttestedNativeExecutor,
    SelfHostedEd25519CommandBoundDeviceAttestor,
)
from aura_music_studio.aura_sec_command_sequence import sequenced_command_nonce
from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_device_attestation import (
    AuraSecAttestedNativePlatformExecutor,
    AuraSecDeviceAttestationStore,
)
from aura_music_studio.aura_sec_native_platform_execution import (
    AuraSecNativePlatformExecutor,
    NativePlatformExecutionEvidence,
)
from aura_music_studio.aura_sec_protocol import ActionType, EXPECTED_RISK, SecurityCommand


class _FakeLinuxAdapter:
    platform = "linux"
    executor_id = "linux-command-bound-native-v1"
    supported_actions = frozenset({ActionType.RUN_QUICK_SCAN})

    def __init__(self):
        self.calls = 0

    def execute(self, command):
        self.calls += 1
        started = command.issued_at + timedelta(seconds=2)
        return NativePlatformExecutionEvidence(
            platform=self.platform,
            executor_id=self.executor_id,
            operation_id=f"operation-{command.command_id}",
            device_id=command.device_id,
            command_id=command.command_id,
            action=command.action,
            outcome="completed",
            result_code="scan_completed",
            started_at=started,
            completed_at=started + timedelta(seconds=1),
            native_proof_digest=hashlib.sha256(
                f"command-bound-native:{command.payload_digest}".encode("utf-8")
            ).hexdigest(),
        )


def _server_signer():
    return SelfHostedEd25519CommandSigner(
        Ed25519PrivateKey.generate(),
        key_id="command-bound-server-key-v1",
    )


def _device_attestor(key_id: str = "command-bound-device-key-v1"):
    return SelfHostedEd25519CommandBoundDeviceAttestor(
        Ed25519PrivateKey.generate(),
        key_id=key_id,
    )


def _signed_command(
    signer,
    *,
    device_id: str,
    command_id: str,
    sequence: int,
    now: datetime,
    policy_version: str = "command-bound-policy-1",
):
    action = ActionType.RUN_QUICK_SCAN
    return signer.sign_command(
        SecurityCommand(
            command_id=command_id,
            device_id=device_id,
            action=action,
            risk=EXPECTED_RISK[action],
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            policy_version=policy_version,
            nonce=sequenced_command_nonce(sequence, entropy=(chr(96 + sequence) * 32)),
            approval_id=None,
            parameters={},
        )
    )


def _stack(tmp_path, *, device_id: str, now: datetime):
    adapter = _FakeLinuxAdapter()
    native = AuraSecNativePlatformExecutor(
        tmp_path / "native.sqlite3",
        adapters={"linux": adapter},
    )
    store = AuraSecDeviceAttestationStore(tmp_path / "attestation.sqlite3")
    attested = AuraSecAttestedNativePlatformExecutor(native, store)
    command_bound = AuraSecCommandBoundAttestedNativeExecutor(attested)
    device = _device_attestor()
    registration = attested.enroll_device_key(
        device_id=device_id,
        device_key_id=device.key_id,
        platform="linux",
        public_key=device.public_key_raw(),
        now=now,
    )
    return adapter, store, attested, command_bound, device, registration


def _issue_and_sign(command_bound, device, command, trusted, device_id, now):
    challenge = command_bound.issue_challenge(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        device_key_id=device.key_id,
        now=now + timedelta(seconds=1),
    )
    assertion = device.sign_challenge(
        challenge,
        attested_at=now + timedelta(seconds=2),
    )
    return challenge, assertion


def test_exact_signed_command_payload_executes_once(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0001"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0001",
        sequence=1,
        now=now,
    )
    adapter, _, _, command_bound, device, registration = _stack(
        tmp_path,
        device_id=device_id,
        now=now,
    )
    challenge, assertion = _issue_and_sign(
        command_bound, device, command, trusted, device_id, now
    )

    assert challenge.command_payload_digest == command.payload_digest
    assert assertion.command_payload_digest == command.payload_digest
    assert assertion.device_assertion.public_key_fingerprint == registration.public_key_fingerprint

    result = command_bound.dispatch(
        command,
        assertion,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=3),
    )
    assert result.native.executed is True
    assert result.native.state == "completed"
    assert adapter.calls == 1


def test_same_command_id_with_different_signed_payload_is_rejected_before_native(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0002"
    command_id = "command-command-bound-0002"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command_a = _signed_command(
        server,
        device_id=device_id,
        command_id=command_id,
        sequence=1,
        now=now,
        policy_version="command-bound-policy-A",
    )
    command_b = _signed_command(
        server,
        device_id=device_id,
        command_id=command_id,
        sequence=1,
        now=now,
        policy_version="command-bound-policy-B",
    )
    assert command_a.command_id == command_b.command_id
    assert command_a.payload_digest != command_b.payload_digest

    adapter, _, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, assertion_a = _issue_and_sign(
        command_bound, device, command_a, trusted, device_id, now
    )

    with pytest.raises(PermissionError, match="different signed-command payload"):
        command_bound.dispatch(
            command_b,
            assertion_a,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0

    good = command_bound.dispatch(
        command_a,
        assertion_a,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=4),
    )
    assert good.native.executed is True
    assert adapter.calls == 1


def test_tampered_binding_signature_does_not_consume_base_challenge(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0003"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0003",
        sequence=1,
        now=now,
    )
    adapter, _, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, good = _issue_and_sign(command_bound, device, command, trusted, device_id, now)
    bad = good.model_copy(
        update={
            "binding_signature_b64": base64.b64encode(b"\x00" * 64).decode("ascii")
        }
    )

    with pytest.raises(PermissionError, match="signature verification failed"):
        command_bound.dispatch(
            command,
            bad,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0

    command_bound.dispatch(
        command,
        good,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=4),
    )
    assert adapter.calls == 1


def test_tampered_binding_digest_fails_before_native_and_before_challenge_consumption(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0004"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0004",
        sequence=1,
        now=now,
    )
    adapter, _, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, good = _issue_and_sign(command_bound, device, command, trusted, device_id, now)
    bad = good.model_copy(update={"binding_payload_digest": "a" * 64})
    if good.binding_payload_digest == "a" * 64:
        bad = good.model_copy(update={"binding_payload_digest": "b" * 64})

    with pytest.raises(PermissionError, match="digest does not match"):
        command_bound.dispatch(
            command,
            bad,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0

    command_bound.dispatch(
        command,
        good,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=4),
    )
    assert adapter.calls == 1


def test_revoked_device_key_blocks_command_binding_before_native(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0005"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0005",
        sequence=1,
        now=now,
    )
    adapter, store, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, assertion = _issue_and_sign(command_bound, device, command, trusted, device_id, now)
    store.revoke_device_key(
        device_id=device_id,
        key_id=device.key_id,
        now=now + timedelta(seconds=3),
    )

    with pytest.raises(PermissionError, match="actively enrolled device key"):
        command_bound.dispatch(
            command,
            assertion,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=4),
        )
    assert adapter.calls == 0


def test_tampering_base_assertion_after_binding_is_still_rejected_fail_closed(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0006"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0006",
        sequence=1,
        now=now,
    )
    adapter, _, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, good = _issue_and_sign(command_bound, device, command, trusted, device_id, now)
    tampered_base = good.device_assertion.model_copy(
        update={"challenge_nonce": "Z" * 48}
    )
    bad = good.model_copy(update={"device_assertion": tampered_base})

    with pytest.raises(PermissionError, match="payload digest does not match"):
        command_bound.dispatch(
            command,
            bad,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0

    command_bound.dispatch(
        command,
        good,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=4),
    )
    assert adapter.calls == 1


def test_untrusted_server_command_cannot_issue_command_bound_challenge(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0007"
    server = _server_signer()
    wrong_server = _server_signer()
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0007",
        sequence=1,
        now=now,
    )
    adapter, _, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)

    with pytest.raises(PermissionError, match="signer"):
        command_bound.issue_challenge(
            command,
            trusted_public_keys={wrong_server.key_id: wrong_server.public_key_raw()},
            expected_device_id=device_id,
            platform="linux",
            device_key_id=device.key_id,
            now=now + timedelta(seconds=1),
        )
    assert adapter.calls == 0


def test_command_payload_digest_tamper_is_rejected_even_with_valid_device_signature(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-command-bound-0008"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-command-bound-0008",
        sequence=1,
        now=now,
    )
    adapter, _, _, command_bound, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    challenge = command_bound.issue_challenge(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        device_key_id=device.key_id,
        now=now + timedelta(seconds=1),
    )
    tampered_digest = "f" * 64 if command.payload_digest != "f" * 64 else "e" * 64
    tampered_challenge = challenge.model_copy(update={"command_payload_digest": tampered_digest})
    valid_for_tampered = device.sign_challenge(
        tampered_challenge,
        attested_at=now + timedelta(seconds=2),
    )

    with pytest.raises(PermissionError, match="different signed-command payload"):
        command_bound.dispatch(
            command,
            valid_for_tampered,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0
