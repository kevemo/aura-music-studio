from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_approval_grants import (
    CONFIRMATION_APPROVAL_TTL_SECONDS,
    STRONG_REAUTH_APPROVAL_TTL_SECONDS,
    action_approval_deadline,
)
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_native_bridge import AuraSecNativeBridge
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "aura-sec-approval-grants.sqlite3")
    signup = accounts.signup(
        "approval.grants@example.test",
        "Approval Grant Member",
        "secure-approval-grant-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="payment-approval-grants",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Approval Grant PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="7" * 64,
    )
    commands = AuraSecCommandStore(accounts, security)
    return accounts, signup.user_id, security, device, commands


def _approve(
    security: AuraSecStore,
    user_id: str,
    device_id: str,
    *,
    action_type: ActionType,
    risk: ActionRisk,
    details: dict | None = None,
):
    proposed = security.propose_action(
        user_id,
        device_id,
        action_type=action_type.value,
        risk_class=risk.value,
        details=details or {},
    )
    return security.approve_action(
        user_id,
        proposed["id"],
        strong_reauth_verified=risk is ActionRisk.STRONG_REAUTH_REQUIRED,
    )


def test_confirmation_approval_creates_immutable_grant_at_approval_time(tmp_path):
    _accounts, user_id, security, device, commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-approval-001"}},
    )

    grant = security.approvals.get(user_id, action["id"])
    approved_at = datetime.fromisoformat(action["approved_at"]).astimezone(timezone.utc)
    expires_at = datetime.fromisoformat(grant["expires_at"]).astimezone(timezone.utc)
    assert grant["id"] == action["id"]
    assert grant["action_id"] == action["id"]
    assert grant["authorization_method"] == "explicit_confirmation"
    assert expires_at - approved_at == timedelta(seconds=CONFIRMATION_APPROVAL_TTL_SECONDS)
    assert len(grant["action_snapshot_digest"]) == 64

    command = commands.issue_approved_action(
        user_id,
        action["id"],
        policy_version="approval-policy-1",
        nonce="approval-command-nonce-0001",
        parameters={"object_id": "object-approval-001"},
    )
    assert command.approval_id == grant["id"]
    assert command.expires_at <= expires_at


def test_strong_reauth_grant_uses_shorter_server_fixed_lifetime(tmp_path):
    _accounts, user_id, security, device, _commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.REMOTE_LOCK,
        risk=ActionRisk.STRONG_REAUTH_REQUIRED,
    )
    grant = security.approvals.get(user_id, action["id"])
    approved_at = datetime.fromisoformat(grant["approved_at"]).astimezone(timezone.utc)
    expires_at = datetime.fromisoformat(grant["expires_at"]).astimezone(timezone.utc)
    assert grant["authorization_method"] == "strong_reauth"
    assert expires_at - approved_at == timedelta(seconds=STRONG_REAUTH_APPROVAL_TTL_SECONDS)


def test_command_store_rejects_approval_at_exact_expiry_boundary(tmp_path, monkeypatch):
    _accounts, user_id, security, device, commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-expired-001"}},
    )
    deadline = action_approval_deadline(action)
    assert deadline is not None
    monkeypatch.setattr("aura_music_studio.aura_sec_command_store._now", lambda: deadline)

    with pytest.raises(PermissionError, match="approval grant expired"):
        commands.issue_approved_action(
            user_id,
            action["id"],
            policy_version="approval-policy-1",
            nonce="expired-command-nonce-0001",
            parameters={"object_id": "object-expired-001"},
        )
    with sqlite3.connect(commands.accounts.db_path) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM aura_sec_commands WHERE action_id=?",
            (action["id"],),
        ).fetchone()[0]
    assert count == 0


def test_command_is_not_issued_when_grant_has_less_than_thirty_seconds_remaining(tmp_path, monkeypatch):
    _accounts, user_id, security, device, commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-near-expiry-001"}},
    )
    deadline = action_approval_deadline(action)
    assert deadline is not None
    monkeypatch.setattr(
        "aura_music_studio.aura_sec_command_store._now",
        lambda: deadline - timedelta(seconds=20),
    )

    with pytest.raises(PermissionError, match="too close to expiry"):
        commands.issue_approved_action(
            user_id,
            action["id"],
            policy_version="approval-policy-1",
            nonce="near-expiry-command-nonce-0001",
            parameters={"object_id": "object-near-expiry-001"},
        )


def test_legacy_high_risk_approval_without_immutable_grant_fails_closed(tmp_path):
    accounts, user_id, security, device, commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-missing-grant-001"}},
    )
    with sqlite3.connect(accounts.db_path) as con:
        con.execute(
            "DELETE FROM aura_sec_action_approval_grants WHERE action_id=?",
            (action["id"],),
        )

    with pytest.raises(PermissionError, match="grant is missing"):
        commands.issue_approved_action(
            user_id,
            action["id"],
            policy_version="approval-policy-1",
            nonce="missing-grant-command-nonce-0001",
            parameters={"object_id": "object-missing-grant-001"},
        )


def test_post_approval_target_mutation_is_detected_before_command_issuance(tmp_path):
    accounts, user_id, security, device, commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-original-001"}},
    )
    with sqlite3.connect(accounts.db_path) as con:
        con.execute(
            "UPDATE aura_sec_actions SET details_json=? WHERE id=?",
            (
                json.dumps({"command_parameters": {"object_id": "object-substituted-999"}}),
                action["id"],
            ),
        )

    with pytest.raises(PermissionError, match="action snapshot"):
        commands.issue_approved_action(
            user_id,
            action["id"],
            policy_version="approval-policy-1",
            nonce="tampered-action-command-nonce-0001",
            parameters={"object_id": "object-substituted-999"},
        )


def test_grant_rows_reject_in_place_mutation(tmp_path):
    accounts, user_id, security, device, _commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-immutable-001"}},
    )
    with sqlite3.connect(accounts.db_path) as con:
        with pytest.raises(sqlite3.DatabaseError, match="approval grants are immutable"):
            con.execute(
                "UPDATE aura_sec_action_approval_grants SET expires_at=? WHERE action_id=?",
                ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), action["id"]),
            )


def test_low_risk_action_does_not_mint_or_require_high_risk_grant(tmp_path):
    _accounts, user_id, security, device, commands = _setup(tmp_path)
    action = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.RUN_QUICK_SCAN,
        risk=ActionRisk.LOW_RISK,
    )
    with pytest.raises(ValueError, match="grant not found"):
        security.approvals.get(user_id, action["id"])

    command = commands.issue_approved_action(
        user_id,
        action["id"],
        policy_version="approval-policy-1",
        nonce="low-risk-command-nonce-0001",
        parameters={},
    )
    assert command.approval_id is None


def test_expired_high_risk_action_does_not_block_newer_authorized_queue_item(tmp_path):
    accounts, user_id, security, device, commands = _setup(tmp_path)
    stale = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT,
        risk=ActionRisk.CONFIRMATION_REQUIRED,
        details={"command_parameters": {"object_id": "object-stale-001"}},
    )
    current = _approve(
        security,
        user_id,
        device["id"],
        action_type=ActionType.RUN_QUICK_SCAN,
        risk=ActionRisk.LOW_RISK,
    )
    bridge = AuraSecNativeBridge(accounts, security, commands)
    stale_deadline = action_approval_deadline(stale)
    assert stale_deadline is not None

    selected = bridge._next_approved_action_id(
        user_id,
        device["id"],
        now=stale_deadline,
    )
    assert selected == current["id"]


def test_failed_grant_recording_rolls_back_action_approval_atomically(tmp_path, monkeypatch):
    _accounts, user_id, security, device, _commands = _setup(tmp_path)
    proposed = security.propose_action(
        user_id,
        device["id"],
        action_type=ActionType.QUARANTINE_OBJECT.value,
        risk_class=ActionRisk.CONFIRMATION_REQUIRED.value,
        details={"command_parameters": {"object_id": "object-rollback-001"}},
    )

    def fail_grant(*args, **kwargs):
        raise PermissionError("simulated approval grant storage failure")

    monkeypatch.setattr(security.approvals, "record_approved_action", fail_grant)
    with pytest.raises(PermissionError, match="simulated approval grant storage failure"):
        security.approve_action(user_id, proposed["id"])

    after = security.get_action(user_id, proposed["id"])
    assert after["status"] == "proposed"
    assert after["approved_at"] is None
