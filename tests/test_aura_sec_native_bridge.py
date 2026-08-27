from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_native_bridge import AuraSecNativeBridge, NativeCommandPoll
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore


def _setup(tmp_path, *, details=None):
    accounts = AccountStore(tmp_path / "aura-sec-native-bridge.sqlite3")
    signup = accounts.signup(
        "native.bridge@example.test",
        "Native Bridge Member",
        "secure-native-bridge-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-native-bridge",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Windows Test PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="d" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
        details=details
        or {
            "summary": "human-readable incident description",
            "command_parameters": {"object_id": "object-verified-001"},
        },
    )
    security.approve_action(signup.user_id, action["id"])
    commands = AuraSecCommandStore(accounts, security)
    bridge = AuraSecNativeBridge(accounts, security, commands)
    return signup.user_id, security, device, action, bridge


def _poll(device_id: str, *, sequence=1, nonce="native-poll-nonce-0001"):
    now = datetime.now(timezone.utc)
    return NativeCommandPoll(
        device_id=device_id,
        sequence=sequence,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        agent_version="0.1.0",
        policy_version="policy-1",
        session_nonce=nonce,
    )


def test_unsigned_native_poll_is_rejected(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    with pytest.raises(PermissionError, match="Unverified"):
        bridge.poll_verified_command(
            user_id,
            _poll(device["id"]),
            signature_verified=False,
        )


def test_verified_poll_issues_one_preapproved_bounded_command(tmp_path):
    user_id, _security, device, action, bridge = _setup(tmp_path)
    result = bridge.poll_verified_command(
        user_id,
        _poll(device["id"]),
        signature_verified=True,
    )
    command = result["command"]
    assert command["action"] == "quarantine_object"
    assert command["approval_id"] == action["id"]
    assert command["parameters"] == {"object_id": "object-verified-001"}
    assert "summary" not in command["parameters"]
    assert result["member_browser_route_exposed"] is False


def test_native_poll_sequence_replay_is_rejected(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    poll = _poll(device["id"])
    bridge.poll_verified_command(user_id, poll, signature_verified=True)
    with pytest.raises(PermissionError, match="sequence|replayed"):
        bridge.poll_verified_command(user_id, poll, signature_verified=True)


def test_next_verified_poll_returns_no_command_after_action_was_issued(tmp_path):
    user_id, _security, device, _action, bridge = _setup(tmp_path)
    bridge.poll_verified_command(user_id, _poll(device["id"]), signature_verified=True)
    result = bridge.poll_verified_command(
        user_id,
        _poll(device["id"], sequence=2, nonce="native-poll-nonce-0002"),
        signature_verified=True,
    )
    assert result["command"] is None


def test_unregistered_or_smuggled_command_parameters_fail_closed(tmp_path):
    user_id, _security, device, _action, bridge = _setup(
        tmp_path,
        details={
            "command_parameters": {
                "object_id": "object-verified-001",
                "shell": "powershell -enc not-allowed",
            }
        },
    )
    with pytest.raises(ValueError, match="requires only"):
        bridge.poll_verified_command(
            user_id,
            _poll(device["id"]),
            signature_verified=True,
        )


def test_revoked_device_cannot_poll_for_commands(tmp_path):
    user_id, security, device, _action, bridge = _setup(tmp_path)
    security.revoke_device(user_id, device["id"])
    with pytest.raises(PermissionError, match="Revoked"):
        bridge.poll_verified_command(
            user_id,
            _poll(device["id"]),
            signature_verified=True,
        )
