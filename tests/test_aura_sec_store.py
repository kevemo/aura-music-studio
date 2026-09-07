from __future__ import annotations

import base64
import hashlib
import inspect
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_protocol import ActionRisk, ActionType, DeviceHeartbeat
from aura_music_studio.aura_sec_store import AuraSecStore, VerifiedHeartbeatSignature


_TEST_HEARTBEAT_SECRET = b"aura-sec-heartbeat-test-secret-v1"


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


def _licensed_device(security: AuraSecStore, user_id: str, *, reference: str, fingerprint: str = "f") -> dict:
    security.activate_verified_purchase(
        user_id,
        sku_id="test-security-sku",
        payment_reference=reference,
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    return security.enroll_attested_device(
        user_id,
        display_name="Test Device",
        platform="windows",
        architecture="x64",
        public_key_fingerprint=fingerprint * 64,
    )


def _heartbeat(
    device_id: str,
    *,
    sequence: int = 1,
    platform: str = "windows",
    architecture: str = "x64",
    policy_version: str = "policy-1",
) -> DeviceHeartbeat:
    now = datetime.now(timezone.utc)
    return DeviceHeartbeat(
        device_id=device_id,
        sequence=sequence,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        agent_version="0.1.0",
        policy_version=policy_version,
        platform=platform,
        architecture=architecture,
        protection_state="healthy",
        report_digest="c" * 64,
        challenge_nonce=f"heartbeat-nonce-{sequence:08d}",
    )


def _heartbeat_signature(heartbeat: DeviceHeartbeat) -> str:
    raw = hashlib.sha256(_TEST_HEARTBEAT_SECRET + heartbeat.signed_payload()).digest()
    return base64.b64encode(raw).decode("ascii")


def _heartbeat_verifier(fingerprint: str, payload: bytes, signature: bytes):
    expected = hashlib.sha256(_TEST_HEARTBEAT_SECRET + payload).digest()
    if not secrets.compare_digest(signature, expected):
        return None
    return VerifiedHeartbeatSignature(
        public_key_fingerprint=fingerprint,
        verifier_id="test-heartbeat-verifier",
        key_algorithm="p256",
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


def _record_heartbeat(security, user_id, heartbeat, *, verifier=_heartbeat_verifier, signature=None):
    return security.record_verified_heartbeat(
        user_id,
        heartbeat,
        signature_b64=signature or _heartbeat_signature(heartbeat),
        signature_verifier=verifier,
    )


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


def test_heartbeat_has_no_boolean_signature_verification_escape_hatch(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-002", fingerprint="b")
    parameters = inspect.signature(security.record_verified_heartbeat).parameters
    assert "signature_verified" not in parameters
    assert "signature_b64" in parameters
    assert "signature_verifier" in parameters

    heartbeat = _heartbeat(device["id"])
    with pytest.raises(PermissionError, match="trusted Aura Sec heartbeat signature verifier"):
        security.record_verified_heartbeat(
            user_id,
            heartbeat,
            signature_b64=_heartbeat_signature(heartbeat),
            signature_verifier=None,
        )


def test_invalid_heartbeat_signature_fails_without_advancing_replay_state(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-002a", fingerprint="b")
    heartbeat = _heartbeat(device["id"])
    invalid = base64.b64encode(b"x" * 32).decode("ascii")
    with pytest.raises(PermissionError, match="signature was not verified"):
        _record_heartbeat(security, user_id, heartbeat, signature=invalid)

    healthy = _record_heartbeat(security, user_id, heartbeat)
    assert healthy["protection_state"] == "healthy"
    assert healthy["last_seen_at"]


def test_verified_heartbeat_key_must_match_enrolled_device_identity(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-002b", fingerprint="c")
    heartbeat = _heartbeat(device["id"])

    def wrong_key_verifier(fingerprint, payload, signature):
        verified = _heartbeat_verifier(fingerprint, payload, signature)
        assert verified is not None
        return VerifiedHeartbeatSignature(
            public_key_fingerprint="d" * 64,
            verifier_id=verified.verifier_id,
            key_algorithm=verified.key_algorithm,
            evidence_digest=verified.evidence_digest,
        )

    with pytest.raises(PermissionError, match="does not match enrolled device identity"):
        _record_heartbeat(security, user_id, heartbeat, verifier=wrong_key_verifier)


def test_heartbeat_signature_is_bound_to_exact_payload_and_sequence(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-002c", fingerprint="d")
    original = _heartbeat(device["id"])
    signature = _heartbeat_signature(original)
    changed = original.model_copy(update={"policy_version": "policy-2"})
    with pytest.raises(PermissionError, match="signature was not verified"):
        _record_heartbeat(security, user_id, changed, signature=signature)

    _record_heartbeat(security, user_id, original)
    with pytest.raises(PermissionError, match="sequence"):
        _record_heartbeat(security, user_id, original)


def test_heartbeat_platform_and_architecture_are_bound_to_enrolment(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-002d", fingerprint="e")
    wrong_platform = _heartbeat(device["id"], platform="macos")
    with pytest.raises(PermissionError, match="platform"):
        _record_heartbeat(security, user_id, wrong_platform)
    wrong_arch = _heartbeat(device["id"], architecture="arm64")
    with pytest.raises(PermissionError, match="architecture"):
        _record_heartbeat(security, user_id, wrong_arch)


def test_revoked_device_cannot_refresh_itself_to_healthy(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-002e", fingerprint="f")
    revoked = security.revoke_device(user_id, device["id"])
    assert revoked["status"] == "revoked"
    assert revoked["protection_state"] == "not_managed"

    heartbeat = _heartbeat(device["id"])
    with pytest.raises(PermissionError, match="Revoked Aura Sec device"):
        _record_heartbeat(security, user_id, heartbeat)


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


def test_store_rejects_risk_downgrade_for_destructive_action(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-003b", fingerprint="e")
    with pytest.raises(ValueError, match="requires risk class strong_reauth_required"):
        security.propose_action(
            user_id,
            device["id"],
            action_type=ActionType.REMOTE_WIPE.value,
            risk_class=ActionRisk.LOW_RISK.value,
            details={"reason": "malicious or buggy downgrade fixture"},
        )


def test_incident_and_high_risk_action_reject_boolean_strong_reauth_shortcut(tmp_path):
    _accounts, security, user_id = _stores(tmp_path)
    device = _licensed_device(security, user_id, reference="verified-payment-004", fingerprint="f")
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
        action_type=ActionType.REMOTE_WIPE.value,
        risk_class=ActionRisk.STRONG_REAUTH_REQUIRED.value,
        details={"reason": "test only"},
    )
    assert action["status"] == "proposed"
    assert action["action_type"] == "remote_wipe"
    assert action["risk_class"] == "strong_reauth_required"

    with pytest.raises(PermissionError, match="Verifier-backed strong re-authentication evidence"):
        security.approve_action(user_id, action["id"], strong_reauth_verified=False)
    with pytest.raises(PermissionError, match="Boolean strong re-authentication flags are not trusted"):
        security.approve_action(user_id, action["id"], strong_reauth_verified=True)
    assert security.get_action(user_id, action["id"])["status"] == "proposed"
