from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_approval import AuraSecApprovalGateway
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType
from aura_music_studio.aura_sec_store import AuraSecStore


PASSWORD = "approval-gateway-password"


def _setup(tmp_path, *, action_type=ActionType.ISOLATE_NETWORK, risk=ActionRisk.CONFIRMATION_REQUIRED):
    accounts = AccountStore(tmp_path / "aura-sec-approval.sqlite3")
    signup = accounts.signup(
        "approval.member@example.test",
        "Approval Member",
        PASSWORD,
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    session = accounts.create_session(signup.user_id)
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="aura-sec-test",
        payment_reference="payment-approval-test",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Approval Test PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="e" * 64,
    )
    action = security.propose_action(
        signup.user_id,
        device["id"],
        action_type=action_type.value,
        risk_class=risk.value,
        details={"command_parameters": {}},
    )
    return accounts, security, AuraSecApprovalGateway(accounts, security), signup.user_id, session, action


def test_confirmation_action_requires_one_time_session_bound_challenge(tmp_path):
    _accounts, security, gateway, user_id, session, action = _setup(tmp_path)
    challenge = gateway.create_challenge(user_id, action["id"], session_token=session)
    result = gateway.approve(
        user_id,
        action["id"],
        session_token=session,
        approval_token=challenge["approval_token"],
    )
    assert result["approved"] is True
    assert result["command_issued"] is False
    assert result["strong_reauthentication_verified"] is False
    assert security.get_action(user_id, action["id"])["status"] == "approved"

    with pytest.raises((PermissionError, ValueError)):
        gateway.approve(
            user_id,
            action["id"],
            session_token=session,
            approval_token=challenge["approval_token"],
        )


def test_challenge_is_bound_to_exact_login_session(tmp_path):
    accounts, _security, gateway, user_id, session, action = _setup(tmp_path)
    other_session = accounts.create_session(user_id)
    challenge = gateway.create_challenge(user_id, action["id"], session_token=session)
    with pytest.raises(PermissionError, match="different member session"):
        gateway.approve(
            user_id,
            action["id"],
            session_token=other_session,
            approval_token=challenge["approval_token"],
        )


def test_new_challenge_supersedes_old_pending_challenge(tmp_path):
    _accounts, _security, gateway, user_id, session, action = _setup(tmp_path)
    old = gateway.create_challenge(user_id, action["id"], session_token=session)
    new = gateway.create_challenge(user_id, action["id"], session_token=session)
    with pytest.raises(PermissionError, match="used or replaced"):
        gateway.approve(
            user_id,
            action["id"],
            session_token=session,
            approval_token=old["approval_token"],
        )
    assert gateway.approve(
        user_id,
        action["id"],
        session_token=session,
        approval_token=new["approval_token"],
    )["approved"] is True


def test_expired_challenge_fails_closed(tmp_path):
    _accounts, _security, gateway, user_id, session, action = _setup(tmp_path)
    challenge = gateway.create_challenge(user_id, action["id"], session_token=session, ttl_minutes=1)
    with pytest.raises(PermissionError, match="expired"):
        gateway.approve(
            user_id,
            action["id"],
            session_token=session,
            approval_token=challenge["approval_token"],
            now=datetime.now(timezone.utc) + timedelta(minutes=2),
        )


def test_remote_wipe_requires_password_reauthentication_before_passkey_enrolment(tmp_path):
    _accounts, security, gateway, user_id, session, action = _setup(
        tmp_path,
        action_type=ActionType.REMOTE_WIPE,
        risk=ActionRisk.STRONG_REAUTH_REQUIRED,
    )
    challenge = gateway.create_challenge(user_id, action["id"], session_token=session)
    assert challenge["strong_reauthentication_required"] is True
    assert challenge["passkey_enrolled"] is False
    assert challenge["passkey_required"] is False

    with pytest.raises(PermissionError, match="Password re-authentication"):
        gateway.approve(
            user_id,
            action["id"],
            session_token=session,
            approval_token=challenge["approval_token"],
        )
    with pytest.raises(PermissionError, match="re-authentication failed"):
        gateway.approve(
            user_id,
            action["id"],
            session_token=session,
            approval_token=challenge["approval_token"],
            password="wrong-password-value",
        )

    result = gateway.approve(
        user_id,
        action["id"],
        session_token=session,
        approval_token=challenge["approval_token"],
        password=PASSWORD,
    )
    assert result["strong_reauthentication_verified"] is True
    assert result["strong_reauthentication_method"] == "password_bootstrap"
    assert result["command_issued"] is False
    assert security.get_action(user_id, action["id"])["status"] == "approved"


def test_enrolled_passkey_blocks_password_downgrade_for_high_risk_action(tmp_path):
    _accounts, security, gateway, user_id, session, action = _setup(
        tmp_path,
        action_type=ActionType.REMOTE_WIPE,
        risk=ActionRisk.STRONG_REAUTH_REQUIRED,
    )

    class FakePasskeys:
        def __init__(self):
            self.consumed = []

        def has_active_credential(self, _user_id):
            return True

        def consume_action_evidence(
            self,
            evidence_user_id,
            evidence_action_id,
            *,
            session_token,
            evidence_id,
            now=None,
        ):
            self.consumed.append(
                (evidence_user_id, evidence_action_id, session_token, evidence_id, now)
            )
            return {"verified": True, "method": "webauthn"}

    fake = FakePasskeys()
    gateway.passkeys = fake
    challenge = gateway.create_challenge(user_id, action["id"], session_token=session)
    assert challenge["passkey_enrolled"] is True
    assert challenge["passkey_required"] is True

    with pytest.raises(PermissionError, match="password downgrade is not allowed"):
        gateway.approve(
            user_id,
            action["id"],
            session_token=session,
            approval_token=challenge["approval_token"],
            password=PASSWORD,
        )

    result = gateway.approve(
        user_id,
        action["id"],
        session_token=session,
        approval_token=challenge["approval_token"],
        strong_reauth_evidence_id="verified-passkey-evidence",
    )
    assert result["approved"] is True
    assert result["strong_reauthentication_method"] == "webauthn"
    assert result["command_issued"] is False
    assert fake.consumed[0][0:4] == (
        user_id,
        action["id"],
        session,
        "verified-passkey-evidence",
    )
    assert security.get_action(user_id, action["id"])["status"] == "approved"


def test_low_risk_action_cannot_be_routed_through_human_approval_gateway(tmp_path):
    _accounts, _security, gateway, user_id, session, action = _setup(
        tmp_path,
        action_type=ActionType.RUN_FULL_SCAN,
        risk=ActionRisk.LOW_RISK,
    )
    with pytest.raises(PermissionError, match="does not use the human approval gateway"):
        gateway.create_challenge(user_id, action["id"], session_token=session)


def test_revoked_device_action_cannot_be_approved(tmp_path):
    _accounts, security, gateway, user_id, session, action = _setup(tmp_path)
    security.revoke_device(user_id, action["device_id"])
    with pytest.raises(PermissionError, match="Revoked"):
        gateway.create_challenge(user_id, action["id"], session_token=session)
