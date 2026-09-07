from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aura_music_studio.aura_sec_command_sequence import sequenced_command_nonce
from aura_music_studio.aura_sec_command_signing import SelfHostedEd25519CommandSigner
from aura_music_studio.aura_sec_native_platform_execution import (
    AuraSecNativePlatformExecutor,
    NativePlatformExecutionEvidence,
    native_platform_execution_evidence_digest,
)
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, EXPECTED_RISK, SecurityCommand


def _signer():
    return SelfHostedEd25519CommandSigner(
        Ed25519PrivateKey.generate(),
        key_id="native-platform-test-key",
    )


def _signed_command(
    signer,
    device_id: str,
    *,
    sequence: int,
    command_id: str,
    action: ActionType = ActionType.RUN_QUICK_SCAN,
):
    now = datetime.now(timezone.utc)
    risk = EXPECTED_RISK[action]
    approval_id = (
        f"approval-{command_id}"
        if risk in {ActionRisk.CONFIRMATION_REQUIRED, ActionRisk.STRONG_REAUTH_REQUIRED}
        else None
    )
    command = SecurityCommand(
        command_id=command_id,
        device_id=device_id,
        action=action,
        risk=risk,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        policy_version="native-platform-policy-1",
        nonce=sequenced_command_nonce(sequence, entropy=(chr(96 + ((sequence - 1) % 26) + 1) * 32)),
        approval_id=approval_id,
        parameters={},
    )
    return signer.sign_command(command)


class _FakeAdapter:
    platform = "linux"
    executor_id = "linux-native-test-v1"
    supported_actions = frozenset({ActionType.RUN_QUICK_SCAN})

    def __init__(self, *, wrong_device: bool = False, fail: bool = False):
        self.calls = 0
        self.wrong_device = wrong_device
        self.fail = fail

    def execute(self, command):
        self.calls += 1
        if self.fail:
            raise OSError("simulated native adapter failure")
        started = command.issued_at + timedelta(seconds=1)
        return NativePlatformExecutionEvidence(
            platform=self.platform,
            executor_id=self.executor_id,
            operation_id=f"operation-{command.command_id}",
            device_id=("device-wrong-binding-0001" if self.wrong_device else command.device_id),
            command_id=command.command_id,
            action=command.action,
            outcome="completed",
            result_code="scan_completed",
            started_at=started,
            completed_at=started + timedelta(seconds=1),
            native_proof_digest=hashlib.sha256(
                f"native-proof:{command.command_id}".encode("utf-8")
            ).hexdigest(),
        )


def test_dispatch_executes_once_and_exact_retry_never_calls_adapter_again(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-native-platform-0001"
    command = _signed_command(
        signer,
        device_id,
        sequence=1,
        command_id="command-native-platform-0001",
    )
    adapter = _FakeAdapter()
    executor = AuraSecNativePlatformExecutor(
        tmp_path / "native-platform.sqlite3",
        adapters={"linux": adapter},
    )

    first = executor.dispatch(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=command.issued_at + timedelta(seconds=3),
    )
    retry = executor.dispatch(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=command.issued_at + timedelta(seconds=4),
    )

    assert adapter.calls == 1
    assert first.executed is True
    assert first.duplicate is False
    assert first.state == "completed"
    assert first.evidence is not None
    assert first.evidence_digest == native_platform_execution_evidence_digest(first.evidence)
    assert retry.executed is False
    assert retry.duplicate is True
    assert retry.state == "completed"
    assert retry.evidence_digest == first.evidence_digest


def test_unsupported_platform_or_action_fails_before_execution_reservation(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-native-platform-0002"
    command = _signed_command(
        signer,
        device_id,
        sequence=1,
        command_id="command-native-platform-0002",
        action=ActionType.RUN_FULL_SCAN,
    )
    adapter = _FakeAdapter()
    executor = AuraSecNativePlatformExecutor(
        tmp_path / "native-platform-unsupported.sqlite3",
        adapters={"linux": adapter},
    )

    with pytest.raises(PermissionError, match="does not implement"):
        executor.dispatch(
            command,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
        )
    with pytest.raises(ValueError, match="record not found"):
        executor.gate.executions.get(device_id=device_id, command_id=command.command_id)
    assert adapter.calls == 0

    with pytest.raises(PermissionError, match="Windows, macOS or Linux"):
        executor.dispatch(
            command,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="browser",
        )


def test_misbound_platform_evidence_fails_terminally_and_retry_is_suppressed(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-native-platform-0003"
    command = _signed_command(
        signer,
        device_id,
        sequence=1,
        command_id="command-native-platform-0003",
    )
    adapter = _FakeAdapter(wrong_device=True)
    executor = AuraSecNativePlatformExecutor(
        tmp_path / "native-platform-binding.sqlite3",
        adapters={"linux": adapter},
    )

    with pytest.raises(PermissionError, match="evidence failed closed"):
        executor.dispatch(
            command,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=command.issued_at + timedelta(seconds=3),
        )

    record = executor.gate.executions.get(device_id=device_id, command_id=command.command_id)
    assert record["state"] == "failed"
    assert record["failure_code"] == "invalid_platform_evidence"

    retry = executor.dispatch(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=command.issued_at + timedelta(seconds=4),
    )
    assert retry.executed is False
    assert retry.duplicate is True
    assert retry.state == "failed"
    assert adapter.calls == 1


def test_adapter_exception_is_terminal_and_never_automatically_retried(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-native-platform-0004"
    command = _signed_command(
        signer,
        device_id,
        sequence=1,
        command_id="command-native-platform-0004",
    )
    adapter = _FakeAdapter(fail=True)
    executor = AuraSecNativePlatformExecutor(
        tmp_path / "native-platform-error.sqlite3",
        adapters={"linux": adapter},
    )

    with pytest.raises(RuntimeError, match="failed closed"):
        executor.dispatch(
            command,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
        )
    record = executor.gate.executions.get(device_id=device_id, command_id=command.command_id)
    assert record["state"] == "failed"
    assert record["failure_code"] == "platform_executor_exception"

    retry = executor.dispatch(
        command,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
    )
    assert retry.executed is False
    assert retry.state == "failed"
    assert adapter.calls == 1


def test_platform_dispatch_retains_signed_sequence_anti_rollback(tmp_path):
    signer = _signer()
    trusted = {signer.key_id: signer.public_key_raw()}
    device_id = "device-native-platform-0005"
    adapter = _FakeAdapter()
    executor = AuraSecNativePlatformExecutor(
        tmp_path / "native-platform-sequence.sqlite3",
        adapters={"linux": adapter},
    )
    ten = _signed_command(
        signer,
        device_id,
        sequence=10,
        command_id="command-native-platform-0010",
    )
    eleven = _signed_command(
        signer,
        device_id,
        sequence=11,
        command_id="command-native-platform-0011",
    )

    executor.dispatch(
        ten,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=ten.issued_at + timedelta(seconds=3),
    )
    executor.dispatch(
        eleven,
        trusted_public_keys=trusted,
        expected_device_id=device_id,
        platform="linux",
        now=eleven.issued_at + timedelta(seconds=3),
    )

    with pytest.raises(PermissionError, match="moved backwards"):
        executor.dispatch(
            ten,
            trusted_public_keys=trusted,
            expected_device_id=device_id,
            platform="linux",
            now=ten.issued_at + timedelta(seconds=4),
        )
    assert adapter.calls == 2


def test_registry_and_evidence_contract_fail_closed(tmp_path):
    adapter = _FakeAdapter()
    adapter.platform = "windows"
    with pytest.raises(ValueError, match="does not match registry key"):
        AuraSecNativePlatformExecutor(
            tmp_path / "native-platform-registry.sqlite3",
            adapters={"linux": adapter},
        )

    now = datetime.now(timezone.utc)
    evidence = NativePlatformExecutionEvidence(
        platform="linux",
        executor_id="linux-native-test-v1",
        operation_id="operation-proof-0001",
        device_id="device-native-proof-0001",
        command_id="command-native-proof-0001",
        action=ActionType.RUN_QUICK_SCAN,
        outcome="completed",
        result_code="ok",
        started_at=now,
        completed_at=now + timedelta(seconds=1),
        native_proof_digest="a" * 64,
    )
    digest = native_platform_execution_evidence_digest(evidence)
    changed = evidence.model_copy(update={"native_proof_digest": "b" * 64})
    assert digest != native_platform_execution_evidence_digest(changed)

    with pytest.raises(ValueError, match="one-hour window"):
        NativePlatformExecutionEvidence(
            **{
                **evidence.model_dump(),
                "completed_at": now + timedelta(hours=2),
            }
        )
