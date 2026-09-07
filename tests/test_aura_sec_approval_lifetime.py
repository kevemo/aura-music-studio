from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_approval_lifetime import (
    APPROVAL_TTL_SECONDS,
    AuraSecApprovalLifetime,
)
from aura_music_studio.aura_sec_command_store import AuraSecCommandStore
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore
from aura_music_studio.aura_sec_strong_reauth import (
    StrongReauthAssertion,
    VerifiedStrongReauthEvidence,
)

_STRONG_SECRET = b"aura-sec-approval-lifetime-strong-reauth-v1"
_SESSION_BINDING = "a" * 64


def _strong_assertion(user_id: str, action_id: str, device_id: str, *, nonce: str):
    now = datetime.now(timezone.utc)
    return StrongReauthAssertion(
        user_id=user_id,
        action_id=action_id,
        device_id=device_id,
        issued_at=now,
        expires_at=now + timedelta(seconds=120),
        challenge_nonce=nonce,
        session_binding_digest=_SESSION_BINDING,
        authentication_context="webauthn",
    )


def _strong_proof(assertion: StrongReauthAssertion) -> str:
    raw = hashlib.sha256(_STRONG_SECRET + assertion.canonical_payload()).digest()
    return base64.b64encode(raw).decode("ascii")


def _strong_verifier(assertion: StrongReauthAssertion, proof: bytes):
    expected = hashlib.sha256(_STRONG_SECRET + assertion.canonical_payload()).digest()
    if not secrets.compare_digest(proof, expected):
        return None
    return VerifiedStrongReauthEvidence(
        subject_user_id=assertion.user_id,
        action_id=assertion.action_id,
        device_id=assertion.device_id,
        session_binding_digest=assertion.session_binding_digest,
        verifier_id="approval-lifetime-test-verifier",
        authentication_method=assertion.authentication_context,
        assurance_level="aal2",
        credential_fingerprint="b" * 64,
        evidence_digest=hashlib.sha256(assertion.canonical_payload()).hexdigest(),
    )


def _approve_strong(security, user_id: str, action_id: str, device_id: str, *, nonce: str):
    assertion = _strong_assertion(user_id, action_id, device_id, nonce=nonce)
    return security.approve_action_with_verified_reauth(
        user_id,
        action_id,
        assertion,
        proof_b64=_strong_proof(assertion),
        evidence_verifier=_strong_verifier,
    )


def _setup(
    tmp_path,
    *,
    action_type: ActionType = ActionType.QUARANTINE_OBJECT,
    risk: ActionRisk = ActionRisk.CONFIRMATION_REQUIRED,
):
    accounts = AccountStore(tmp_path / "approval-lifetime.sqlite3")
    signup = accounts.signup(
        "approval.member@example.test",
        "Approval Member",
        "secure-approval-test-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="approval-payment",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Approval Test Device",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="f" * 64,
    )
    details = {}
    if action_type is ActionType.QUARANTINE_OBJECT:
        details = {"command_parameters": {"object_id": "approval-object-1"}}
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=action_type.value,
        risk_class=risk.value,
        details=details,
    )
    if risk is ActionRisk.STRONG_REAUTH_REQUIRED:
        approved = _approve_strong(
            security,
            signup.user_id,
            action["id"],
            device["id"],
            nonce="approval-lifetime-strong-0001",
        )
    else:
        approved = security.approve_action(signup.user_id, action["id"])
    policy = AuraSecApprovalLifetime(accounts, security)
    commands = AuraSecCommandStore(accounts, security, policy)
    return accounts, security, policy, commands, signup.user_id, device, approved


def _approved_at(action: dict) -> datetime:
    return datetime.fromisoformat(action["approved_at"]).astimezone(timezone.utc)


def test_risk_classes_have_fixed_server_controlled_approval_lifetimes(tmp_path):
    _accounts, _security, policy, _commands, _user_id, _device, approved = _setup(tmp_path)
    window = policy.window(approved, now=_approved_at(approved))
    assert APPROVAL_TTL_SECONDS[ActionRisk.CONFIRMATION_REQUIRED] == 600
    assert APPROVAL_TTL_SECONDS[ActionRisk.STRONG_REAUTH_REQUIRED] == 300
    assert APPROVAL_TTL_SECONDS[ActionRisk.LOW_RISK] == 1800
    assert APPROVAL_TTL_SECONDS[ActionRisk.READ_ONLY] == 1800
    assert window.expires_at == window.approved_at + timedelta(seconds=600)


def test_expired_approval_is_invalidated_and_cannot_issue_command(tmp_path):
    _accounts, security, policy, commands, user_id, _device, approved = _setup(tmp_path)
    expired_now = _approved_at(approved) + timedelta(seconds=601)

    with pytest.raises(PermissionError, match="explicit re-authorization"):
        commands.issue_approved_action(
            user_id,
            approved["id"],
            policy_version="policy-1",
            nonce="approval-expired-nonce-0001",
            parameters={"object_id": "approval-object-1"},
            now=expired_now,
        )

    assert security.get_action(user_id, approved["id"])["status"] == "expired"
    with pytest.raises(ValueError, match="Only proposed actions"):
        security.approve_action(user_id, approved["id"])


def test_legacy_approval_without_trustworthy_timestamp_fails_closed(tmp_path):
    accounts, security, policy, _commands, user_id, _device, approved = _setup(tmp_path)
    with sqlite3.connect(accounts.db_path) as con:
        con.execute(
            "UPDATE aura_sec_actions SET approved_at=NULL WHERE user_id=? AND id=?",
            (user_id, approved["id"]),
        )

    with pytest.raises(PermissionError, match="no trustworthy approval timestamp"):
        policy.require_fresh(user_id, approved["id"])
    assert security.get_action(user_id, approved["id"])["status"] == "expired"


def test_command_expiry_is_capped_by_remaining_approval_window(tmp_path):
    _accounts, _security, _policy, commands, user_id, _device, approved = _setup(tmp_path)
    approved_at = _approved_at(approved)
    issue_at = approved_at + timedelta(minutes=9)

    command = commands.issue_approved_action(
        user_id,
        approved["id"],
        policy_version="policy-1",
        nonce="approval-capped-nonce-0001",
        parameters={"object_id": "approval-object-1"},
        ttl_seconds=300,
        now=issue_at,
    )

    assert command.issued_at == issue_at
    assert command.expires_at == approved_at + timedelta(minutes=10)
    assert (command.expires_at - command.issued_at).total_seconds() == 60


def test_near_expiry_approval_cannot_create_tiny_last_second_command(tmp_path):
    _accounts, security, _policy, commands, user_id, _device, approved = _setup(tmp_path)
    issue_at = _approved_at(approved) + timedelta(minutes=9, seconds=45)

    with pytest.raises(PermissionError, match="too close to expiry|re-authorization"):
        commands.issue_approved_action(
            user_id,
            approved["id"],
            policy_version="policy-1",
            nonce="approval-too-close-nonce-0001",
            parameters={"object_id": "approval-object-1"},
            now=issue_at,
        )
    assert security.get_action(user_id, approved["id"])["status"] == "expired"


def test_strong_reauth_action_must_be_explicitly_reauthorized_and_reapproved(tmp_path):
    _accounts, security, policy, _commands, user_id, device, approved = _setup(
        tmp_path,
        action_type=ActionType.REMOTE_WIPE,
        risk=ActionRisk.STRONG_REAUTH_REQUIRED,
    )
    future = _approved_at(approved) + timedelta(seconds=301)

    with pytest.raises(PermissionError, match="explicit re-authorization"):
        policy.require_fresh(user_id, approved["id"], now=future)
    assert security.get_action(user_id, approved["id"])["status"] == "expired"

    proposed_again = policy.reauthorize_action(user_id, approved["id"])
    assert proposed_again["status"] == "proposed"
    assert proposed_again["approved_at"] is None

    with pytest.raises(PermissionError, match="Verifier-backed strong re-authentication evidence"):
        security.approve_action(
            user_id,
            approved["id"],
            strong_reauth_verified=False,
        )
    with pytest.raises(PermissionError, match="Boolean strong re-authentication flags are not trusted"):
        security.approve_action(
            user_id,
            approved["id"],
            strong_reauth_verified=True,
        )

    approved_again = _approve_strong(
        security,
        user_id,
        approved["id"],
        device["id"],
        nonce="approval-lifetime-strong-0002",
    )
    assert approved_again["status"] == "approved"
    assert approved_again["approved_at"]
    assert approved_again["strong_reauth_verifier"] == "approval-lifetime-test-verifier"


def test_only_expired_never_executed_actions_can_enter_reauthorization_flow(tmp_path):
    _accounts, _security, policy, _commands, user_id, _device, approved = _setup(tmp_path)
    with pytest.raises(ValueError, match="Only expired Aura Sec actions"):
        policy.reauthorize_action(user_id, approved["id"])
