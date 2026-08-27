from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, CommandReceipt
from aura_music_studio.aura_sec_store import AuraSecStore


def _setup(tmp_path, *, action_type=ActionType.QUARANTINE_OBJECT, risk=ActionRisk.CONFIRMATION_REQUIRED):
    accounts = AccountStore(tmp_path / "aura-sec-command.sqlite3")
    signup = accounts.signup(
        "command.member@example.test",
        "Command Member",
        "secure-command-test-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-command-test",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Test Device",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="a" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=action_type.value,
        risk_class=risk.value,
        details={"fixture": True},
    )
    approved = security.approve_action(
        signup.user_id,
        action["id"],
        strong_reauth_verified=risk is ActionRisk.STRONG_REAUTH_REQUIRED,
    )
    return accounts, security, AuraSecCommandStore(accounts, security), signup.user_id, device, approved


def test_unapproved_action_cannot_be_issued(tmp_path):
    accounts = AccountStore(tmp_path / "unapproved.sqlite3")
    signup = accounts.signup("u@example.test", "User", "secure-password-123", "free")
    accounts.decide_membership(signup.approval_token, "approve", "owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-unapproved",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="b" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
    )
    commands = AuraSecCommandStore(accounts, security)
    with pytest.raises(PermissionError, match="must be approved"):
        commands.issue_approved_action(
            signup.user_id,
            action["id"],
            policy_version="policy-1",
            nonce="nonce-1234567890abcdef",
            parameters={"object_id": "quarantine-object-1"},
        )


def test_approved_action_issues_only_bounded_protocol_command(tmp_path):
    _accounts, _security, commands, user_id, device, action = _setup(tmp_path)
    command = commands.issue_approved_action(
        user_id,
        action["id"],
        policy_version="policy-1",
        nonce="nonce-1234567890abcdef",
        parameters={"object_id": "quarantine-object-1"},
    )
    assert command.action is ActionType.QUARANTINE_OBJECT
    assert command.risk is ActionRisk.CONFIRMATION_REQUIRED
    assert command.approval_id == action["id"]
    assert command.device_id == device["id"]
    assert "shell" not in command.parameters


def test_same_action_cannot_be_issued_twice(tmp_path):
    _accounts, _security, commands, user_id, _device, action = _setup(tmp_path)
    commands.issue_approved_action(
        user_id,
        action["id"],
        policy_version="policy-1",
        nonce="nonce-1234567890abcdef",
    )
    with pytest.raises(ValueError, match="already issued|replayed"):
        commands.issue_approved_action(
            user_id,
            action["id"],
            policy_version="policy-1",
            nonce="nonce-2234567890abcdef",
        )


def test_unverified_receipt_is_rejected(tmp_path):
    _accounts, _security, commands, user_id, device, action = _setup(tmp_path)
    command = commands.issue_approved_action(
        user_id,
        action["id"],
        policy_version="policy-1",
        nonce="nonce-1234567890abcdef",
    )
    receipt = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=datetime.now(timezone.utc),
        result_code="accepted",
    )
    with pytest.raises(PermissionError, match="Unverified"):
        commands.accept_verified_receipt(user_id, receipt, signature_verified=False)


def test_receipt_lifecycle_requires_valid_state_transitions_and_evidence(tmp_path):
    _accounts, security, commands, user_id, device, action = _setup(tmp_path)
    command = commands.issue_approved_action(
        user_id,
        action["id"],
        policy_version="policy-1",
        nonce="nonce-1234567890abcdef",
    )
    now = datetime.now(timezone.utc)
    received = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="received",
        occurred_at=now,
        result_code="accepted",
    )
    assert commands.accept_verified_receipt(user_id, received, signature_verified=True)["status"] == "received"

    executed = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="executed",
        occurred_at=now,
        result_code="executed",
    )
    assert commands.accept_verified_receipt(user_id, executed, signature_verified=True)["status"] == "executed"
    assert security.get_action(user_id, action["id"])["status"] == "executed"

    verified = CommandReceipt(
        command_id=command.command_id,
        device_id=device["id"],
        status="verified",
        occurred_at=now,
        result_code="post_state_verified",
        evidence_digest="c" * 64,
    )
    assert commands.accept_verified_receipt(user_id, verified, signature_verified=True)["status"] == "verified"
    assert security.get_action(user_id, action["id"])["status"] == "verified"

    with pytest.raises(ValueError, match="Invalid Aura Sec command transition"):
        commands.accept_verified_receipt(user_id, received, signature_verified=True)
