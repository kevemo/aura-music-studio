from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_store import AuraSecStore


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "aura-sec.sqlite3")
    signup = accounts.signup(
        "security.member@example.test",
        "Security Member",
        "a-secure-test-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    return accounts, AuraSecStore(accounts), signup.user_id


def test_creative_membership_never_implies_aura_sec_purchase(tmp_path):
    accounts, security, user_id = _stores(tmp_path)
    user = accounts.get_user(user_id)
    assert user["status"] == "active"
    assert user["plan_id"] == "free"
    assert security.licence(user_id)["status"] == "not_purchased"
    assert security.licence(user_id)["device_limit"] == 0


def test_verified_purchase_is_separate_and_payment_reference_cannot_be_reused(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    licence = security.activate_verified_purchase(
        user_id,
        sku_id="test-security-sku",
        payment_reference="verified-payment-001",
        device_limit=3,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    assert licence["status"] == "active"
    assert licence["sku_id"] == "test-security-sku"
    assert licence["device_limit"] == 3

    with pytest.raises(ValueError, match="already been used"):
        security.activate_verified_purchase(
            user_id,
            sku_id="test-security-sku",
            payment_reference="verified-payment-001",
            device_limit=3,
            period_days=31,
            verified_by="test-billing-verifier",
        )


def test_device_enrolment_requires_separate_active_licence(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    with pytest.raises(PermissionError, match="Active Aura Sec licence"):
        security.enroll_attested_device(
            user_id,
            display_name="Test Windows PC",
            platform="windows",
            architecture="x64",
            public_key_fingerprint="a" * 64,
        )


def test_verified_device_heartbeat_is_fail_closed(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    security.activate_verified_purchase(
        user_id,
        sku_id="test-security-sku",
        payment_reference="verified-payment-002",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    device = security.enroll_attested_device(
        user_id,
        display_name="Test Windows PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="b" * 64,
    )
    assert device["protection_state"] == "awaiting_heartbeat"

    with pytest.raises(PermissionError, match="Unverified Aura Sec heartbeat"):
        security.record_verified_heartbeat(
            user_id,
            device["id"],
            signature_verified=False,
            agent_version="0.1.0",
            policy_version="policy-1",
            report_digest="c" * 64,
            protection_state="healthy",
        )

    healthy = security.record_verified_heartbeat(
        user_id,
        device["id"],
        signature_verified=True,
        agent_version="0.1.0",
        policy_version="policy-1",
        report_digest="c" * 64,
        protection_state="healthy",
    )
    assert healthy["protection_state"] == "healthy"
    assert healthy["last_seen_at"]


def test_device_limit_is_enforced(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    security.activate_verified_purchase(
        user_id,
        sku_id="test-security-sku",
        payment_reference="verified-payment-003",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    security.enroll_attested_device(
        user_id,
        display_name="Device One",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="d" * 64,
    )
    with pytest.raises(PermissionError, match="device limit"):
        security.enroll_attested_device(
            user_id,
            display_name="Device Two",
            platform="macos",
            architecture="arm64",
            public_key_fingerprint="e" * 64,
        )


def test_incident_and_high_risk_action_require_explicit_approval(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    security.activate_verified_purchase(
        user_id,
        sku_id="test-security-sku",
        payment_reference="verified-payment-004",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    device = security.enroll_attested_device(
        user_id,
        display_name="Device One",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="f" * 64,
    )
    incident = security.create_incident(
        user_id,
        device["id"],
        severity="critical",
        title="Ransomware-like mass file changes",
        detection_id="AURA-RANSOM-TEST",
        confidence=0.98,
        summary={"fixture": True},
    )
    action = security.propose_action(
        user_id,
        device["id"],
        incident_id=incident["id"],
        action_type="remote_device_wipe",
        risk_class="strong_reauth_required",
        details={"reason": "test only"},
    )
    assert action["status"] == "proposed"

    with pytest.raises(PermissionError, match="Strong re-authentication"):
        security.approve_action(user_id, action["id"], strong_reauth_verified=False)

    approved = security.approve_action(user_id, action["id"], strong_reauth_verified=True)
    assert approved["status"] == "approved"
    assert approved["approved_at"]
