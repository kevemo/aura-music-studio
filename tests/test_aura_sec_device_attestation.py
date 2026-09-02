from __future__ import annotations

import base64
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_sequence import sequenced_command_nonce
from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_device_attestation import (
    AuraSecAttestedNativePlatformExecutor,
    AuraSecDeviceAttestationStore,
    SelfHostedEd25519DeviceAttestor,
)
from aura_music_studio.aura_sec_native_platform_execution import (
    AuraSecNativePlatformExecutor,
    NativePlatformExecutionEvidence,
)
from aura_music_studio.aura_sec_protocol import ActionType, EXPECTED_RISK, SecurityCommand


class _FakeLinuxAdapter:
    platform = "linux"
    executor_id = "linux-attested-native-v1"
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
                f"attested-native-proof:{command.command_id}".encode("utf-8")
            ).hexdigest(),
        )


def _server_signer():
    return SelfHostedEd25519CommandSigner(
        Ed25519PrivateKey.generate(),
        key_id="attestation-server-key-v1",
    )


def _device_attestor(key_id: str = "device-key-v1"):
    return SelfHostedEd25519DeviceAttestor(
        Ed25519PrivateKey.generate(),
        key_id=key_id,
    )


def _signed_command(signer, *, device_id: str, command_id: str, sequence: int, now: datetime):
    action = ActionType.RUN_QUICK_SCAN
    command = SecurityCommand(
        command_id=command_id,
        device_id=device_id,
        action=action,
        risk=EXPECTED_RISK[action],
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        policy_version="attested-native-policy-1",
        nonce=sequenced_command_nonce(sequence, entropy=(chr(96 + sequence) * 32)),
        approval_id=None,
        parameters={},
    )
    return signer.sign_command(command)


def _stack(tmp_path, *, device_id: str, now: datetime):
    adapter = _FakeLinuxAdapter()
    native = AuraSecNativePlatformExecutor(
        tmp_path / "native.sqlite3",
        adapters={"linux": adapter},
    )
    store = AuraSecDeviceAttestationStore(tmp_path / "attestation.sqlite3")
    attested = AuraSecAttestedNativePlatformExecutor(native, store)
    device = _device_attestor()
    registration = attested.enroll_device_key(
        device_id=device_id,
        device_key_id=device.key_id,
        platform="linux",
        public_key=device.public_key_raw(),
        now=now,
    )
    return adapter, native, store, attested, device, registration


def _challenge_and_assertion(attested, device, command, trusted, device_id, now):
    challenge = attested.issue_challenge(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        device_key_id=device.key_id,
        now=now + timedelta(seconds=1),
    )
    assertion = device.sign_challenge(challenge, attested_at=now + timedelta(seconds=2))
    return challenge, assertion


def test_valid_device_assertion_is_consumed_before_one_native_execution(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0001"
    command_id = "command-attested-native-0001"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(server, device_id=device_id, command_id=command_id, sequence=1, now=now)
    adapter, _, store, attested, device, registration = _stack(
        tmp_path, device_id=device_id, now=now
    )
    _, assertion = _challenge_and_assertion(attested, device, command, trusted, device_id, now)

    result = attested.dispatch(
        command,
        assertion,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=3),
    )

    assert adapter.calls == 1
    assert result.native.executed is True
    assert result.native.state == "completed"
    assert result.attestation.device_id == device_id
    assert result.attestation.command_id == command_id
    assert result.attestation.executor_id == adapter.executor_id
    assert result.attestation.public_key_fingerprint == registration.public_key_fingerprint
    assert len(result.attestation.evidence_digest) == 64

    with pytest.raises(PermissionError, match="already consumed"):
        store.verify_and_consume(
            assertion,
            expected_device_id=device_id,
            expected_command_id=command_id,
            expected_platform="linux",
            expected_executor_id=adapter.executor_id,
            now=now + timedelta(seconds=4),
        )


def test_replayed_assertion_never_reaches_native_adapter_twice(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0002"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0002",
        sequence=1,
        now=now,
    )
    adapter, _, _, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, assertion = _challenge_and_assertion(attested, device, command, trusted, device_id, now)

    attested.dispatch(
        command,
        assertion,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=3),
    )
    with pytest.raises(PermissionError, match="already consumed"):
        attested.dispatch(
            command,
            assertion,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=4),
        )
    assert adapter.calls == 1


def test_expired_challenge_fails_before_native_execution(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0003"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0003",
        sequence=1,
        now=now,
    )
    adapter, _, _, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    challenge = attested.issue_challenge(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        device_key_id=device.key_id,
        ttl_seconds=30,
        now=now + timedelta(seconds=1),
    )
    assertion = device.sign_challenge(challenge, attested_at=now + timedelta(seconds=2))

    with pytest.raises(PermissionError, match="challenge has expired"):
        attested.dispatch(
            command,
            assertion,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=32),
        )
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("device_id", "device-attested-native-9999", "different device"),
        ("command_id", "command-attested-native-9999", "different command"),
        ("platform", "windows", "different platform"),
        ("executor_id", "linux-attested-native-wrong", "different native executor"),
    ],
)
def test_wrong_attestation_binding_fails_before_native(tmp_path, field, value, message):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0004"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0004",
        sequence=1,
        now=now,
    )
    adapter, _, _, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, good = _challenge_and_assertion(attested, device, command, trusted, device_id, now)
    bad = good.model_copy(update={field: value})

    with pytest.raises(PermissionError, match=message):
        attested.dispatch(
            command,
            bad,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0


def test_bad_signature_does_not_consume_challenge_and_good_signature_still_works(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0005"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0005",
        sequence=1,
        now=now,
    )
    adapter, _, _, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, good = _challenge_and_assertion(attested, device, command, trusted, device_id, now)
    bad = good.model_copy(
        update={"signature_b64": base64.b64encode(b"\x00" * 64).decode("ascii")}
    )

    with pytest.raises(PermissionError, match="signature verification failed"):
        attested.dispatch(
            command,
            bad,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=3),
        )
    assert adapter.calls == 0

    attested.dispatch(
        command,
        good,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=now + timedelta(seconds=4),
    )
    assert adapter.calls == 1


def test_revoked_device_key_invalidates_existing_challenge_and_cannot_reactivate(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0006"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0006",
        sequence=1,
        now=now,
    )
    adapter, _, store, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    _, assertion = _challenge_and_assertion(attested, device, command, trusted, device_id, now)
    revoked = store.revoke_device_key(
        device_id=device_id,
        key_id=device.key_id,
        now=now + timedelta(seconds=3),
    )
    assert revoked.state == "revoked"

    with pytest.raises(PermissionError, match="not actively trusted"):
        attested.dispatch(
            command,
            assertion,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=4),
        )
    assert adapter.calls == 0

    with pytest.raises(PermissionError, match="cannot be silently reactivated"):
        attested.enroll_device_key(
            device_id=device_id,
            device_key_id=device.key_id,
            platform="linux",
            public_key=device.public_key_raw(),
            now=now + timedelta(seconds=5),
        )


def test_attestation_timestamp_skew_fails_closed_before_native(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0007"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0007",
        sequence=1,
        now=now,
    )
    adapter, _, _, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)
    challenge = attested.issue_challenge(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        device_key_id=device.key_id,
        now=now + timedelta(seconds=1),
    )
    future = device.sign_challenge(challenge, attested_at=now + timedelta(seconds=90))

    with pytest.raises(PermissionError, match="too far in the future"):
        attested.dispatch(
            command,
            future,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=now + timedelta(seconds=2),
        )
    assert adapter.calls == 0


def test_untrusted_server_command_cannot_issue_device_challenge(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0008"
    server = _server_signer()
    wrong_server = _server_signer()
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0008",
        sequence=1,
        now=now,
    )
    adapter, _, _, attested, device, _ = _stack(tmp_path, device_id=device_id, now=now)

    with pytest.raises(PermissionError, match="signer"):
        attested.issue_challenge(
            command,
            trusted_public_keys={wrong_server.key_id: wrong_server.public_key_raw()},
            expected_device_id=device_id,
            platform="linux",
            device_key_id=device.key_id,
            now=now + timedelta(seconds=1),
        )
    assert adapter.calls == 0


def test_raw_challenge_nonce_is_not_persisted(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0009"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0009",
        sequence=1,
        now=now,
    )
    adapter, _, store, attested, device, registration = _stack(
        tmp_path, device_id=device_id, now=now
    )
    assert registration.executor_id == adapter.executor_id
    challenge = attested.issue_challenge(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        device_key_id=device.key_id,
        now=now + timedelta(seconds=1),
    )

    conn = sqlite3.connect(store.db_path)
    row = conn.execute(
        "SELECT nonce_digest FROM aura_sec_device_attestation_challenges WHERE challenge_id = ?",
        (challenge.challenge_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == hashlib.sha256(challenge.challenge_nonce.encode("ascii")).hexdigest()
    assert challenge.challenge_nonce not in store.db_path.read_bytes().decode("latin1", errors="ignore")


def test_executor_binding_is_enforced_when_challenge_is_issued(tmp_path):
    now = datetime.now(timezone.utc)
    device_id = "device-attested-native-0010"
    server = _server_signer()
    trusted = {server.key_id: server.public_key_raw()}
    command = _signed_command(
        server,
        device_id=device_id,
        command_id="command-attested-native-0010",
        sequence=1,
        now=now,
    )
    adapter = _FakeLinuxAdapter()
    native = AuraSecNativePlatformExecutor(
        tmp_path / "native-executor-bound.sqlite3",
        adapters={"linux": adapter},
    )
    store = AuraSecDeviceAttestationStore(tmp_path / "attestation-executor-bound.sqlite3")
    attested = AuraSecAttestedNativePlatformExecutor(native, store)
    device = _device_attestor(key_id="device-key-executor-bound")
    store.enroll_device_key(
        device_id=device_id,
        key_id=device.key_id,
        platform="linux",
        executor_id="linux-other-executor-v1",
        public_key=device.public_key_raw(),
        now=now,
    )

    with pytest.raises(PermissionError, match="different executor"):
        attested.issue_challenge(
            command,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            device_key_id=device.key_id,
            now=now + timedelta(seconds=1),
        )
    assert adapter.calls == 0
