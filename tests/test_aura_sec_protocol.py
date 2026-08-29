from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aura_music_studio.aura_sec_protocol import (
    ActionRisk,
    ActionType,
    CommandReceipt,
    DeviceCapability,
    DeviceHeartbeat,
    SecurityCommand,
)


NOW = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
DEVICE = "device_1234567890abcdef"
COMMAND = "command_1234567890abcdef"
APPROVAL = "approval_1234567890abcdef"
NONCE = "nonce-1234567890abcdef"


def test_protocol_has_no_arbitrary_shell_action():
    values = {item.value for item in ActionType}
    assert "shell" not in values
    assert "run_command" not in values
    assert "powershell" not in values
    assert "execute_script" not in values


def test_heartbeat_is_short_lived_and_requires_unique_capabilities():
    heartbeat = DeviceHeartbeat(
        device_id=DEVICE,
        sequence=1,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
        agent_version="0.1.0",
        policy_version="policy-1",
        platform="windows",
        architecture="x64",
        protection_state="healthy",
        capabilities=[DeviceCapability(id="malware.scan", state="available")],
        report_digest="a" * 64,
        challenge_nonce=NONCE,
    )
    assert heartbeat.sequence == 1
    assert heartbeat.protection_state.value == "healthy"

    with pytest.raises(ValidationError, match="five minutes"):
        DeviceHeartbeat(
            device_id=DEVICE,
            sequence=2,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=6),
            agent_version="0.1.0",
            policy_version="policy-1",
            platform="windows",
            architecture="x64",
            protection_state="healthy",
            capabilities=[],
            report_digest="a" * 64,
            challenge_nonce=NONCE,
        )


def test_destructive_action_cannot_be_downgraded_to_low_risk():
    with pytest.raises(ValidationError, match="requires risk class strong_reauth_required"):
        SecurityCommand(
            command_id=COMMAND,
            device_id=DEVICE,
            action=ActionType.REMOTE_WIPE,
            risk=ActionRisk.LOW_RISK,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            policy_version="policy-1",
            nonce=NONCE,
            parameters={},
        )


def test_high_risk_command_requires_approval_id():
    with pytest.raises(ValidationError, match="approved action id"):
        SecurityCommand(
            command_id=COMMAND,
            device_id=DEVICE,
            action=ActionType.REMOTE_WIPE,
            risk=ActionRisk.STRONG_REAUTH_REQUIRED,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            policy_version="policy-1",
            nonce=NONCE,
            parameters={},
        )


def test_approved_high_risk_command_has_bounded_lifetime():
    command = SecurityCommand(
        command_id=COMMAND,
        device_id=DEVICE,
        action=ActionType.REMOTE_WIPE,
        risk=ActionRisk.STRONG_REAUTH_REQUIRED,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        policy_version="policy-1",
        nonce=NONCE,
        approval_id=APPROVAL,
        parameters={"scope": "managed-user-data"},
    )
    assert command.action is ActionType.REMOTE_WIPE

    with pytest.raises(ValidationError, match="fifteen minutes"):
        SecurityCommand(
            command_id="command_1234567890abcdee",
            device_id=DEVICE,
            action=ActionType.REMOTE_WIPE,
            risk=ActionRisk.STRONG_REAUTH_REQUIRED,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=16),
            policy_version="policy-1",
            nonce=NONCE,
            approval_id=APPROVAL,
            parameters={},
        )


def test_verified_receipt_requires_evidence_digest():
    with pytest.raises(ValidationError, match="evidence digest"):
        CommandReceipt(
            command_id=COMMAND,
            device_id=DEVICE,
            status="verified",
            occurred_at=NOW,
            result_code="success",
            detail="Agent says completed but supplied no verification evidence.",
        )

    receipt = CommandReceipt(
        command_id=COMMAND,
        device_id=DEVICE,
        status="verified",
        occurred_at=NOW,
        result_code="success",
        evidence_digest="b" * 64,
        detail="Verified by post-action state check.",
    )
    assert receipt.status == "verified"
