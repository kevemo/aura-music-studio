from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_heartbeat_gateway import AuraSecHeartbeatGateway
from aura_music_studio.aura_sec_protocol import DeviceHeartbeat
from aura_music_studio.aura_sec_store import AuraSecStore, VerifiedHeartbeatSignature


_TEST_SECRET = b"aura-sec-heartbeat-gateway-test-secret-v1"


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "aura-sec-heartbeat-gateway.sqlite3")
    signup = accounts.signup(
        "heartbeat.gateway@example.test",
        "Heartbeat Gateway",
        "a-secure-test-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="test-security-sku",
        payment_reference="verified-heartbeat-gateway-payment",
        device_limit=1,
        period_days=31,
        verified_by="test-billing-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Gateway Test PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="a" * 64,
    )
    return signup.user_id, security, device, AuraSecHeartbeatGateway(accounts, security)


def _heartbeat(device_id: str, challenge: str, *, sequence: int = 1) -> DeviceHeartbeat:
    now = datetime.now(timezone.utc)
    return DeviceHeartbeat(
        device_id=device_id,
        sequence=sequence,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        agent_version="0.1.0",
        policy_version="policy-1",
        platform="windows",
        architecture="x64",
        protection_state="healthy",
        report_digest="b" * 64,
        challenge_nonce=challenge,
    )


def _signature(heartbeat: DeviceHeartbeat) -> str:
    raw = hashlib.sha256(_TEST_SECRET + heartbeat.signed_payload()).digest()
    return base64.b64encode(raw).decode("ascii")


def _verifier(fingerprint: str, payload: bytes, signature: bytes):
    expected = hashlib.sha256(_TEST_SECRET + payload).digest()
    if not secrets.compare_digest(signature, expected):
        return None
    return VerifiedHeartbeatSignature(
        public_key_fingerprint=fingerprint,
        verifier_id="test-heartbeat-gateway-verifier",
        key_algorithm="p256",
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


def test_challenge_is_native_only_one_time_and_plaintext_is_not_persisted(tmp_path):
    user_id, _security, device, gateway = _setup(tmp_path)
    issued = gateway.issue_challenge(user_id, device["id"])
    assert issued["one_time"] is True
    assert issued["member_browser_route_exposed"] is False
    assert len(issued["challenge_nonce"]) >= 32

    with gateway._connect() as con:
        row = con.execute(
            "SELECT challenge_hash,status FROM aura_sec_heartbeat_challenges WHERE id=?",
            (issued["challenge_id"],),
        ).fetchone()
    assert row["status"] == "pending"
    assert row["challenge_hash"] == hashlib.sha256(
        issued["challenge_nonce"].encode("utf-8")
    ).hexdigest()
    assert row["challenge_hash"] != issued["challenge_nonce"]


def test_unissued_challenge_is_rejected(tmp_path):
    user_id, _security, device, gateway = _setup(tmp_path)
    heartbeat = _heartbeat(device["id"], "unissued-heartbeat-nonce-0001")
    with pytest.raises(PermissionError, match="not issued"):
        gateway.verify_and_record(
            user_id,
            heartbeat,
            signature_b64=_signature(heartbeat),
            signature_verifier=_verifier,
        )


def test_bad_signature_does_not_burn_valid_challenge(tmp_path):
    user_id, _security, device, gateway = _setup(tmp_path)
    issued = gateway.issue_challenge(user_id, device["id"])
    heartbeat = _heartbeat(device["id"], issued["challenge_nonce"])
    invalid = base64.b64encode(b"x" * 32).decode("ascii")

    with pytest.raises(PermissionError, match="signature was not verified"):
        gateway.verify_and_record(
            user_id,
            heartbeat,
            signature_b64=invalid,
            signature_verifier=_verifier,
        )

    result = gateway.verify_and_record(
        user_id,
        heartbeat,
        signature_b64=_signature(heartbeat),
        signature_verifier=_verifier,
    )
    assert result["challenge_consumed"] is True
    assert result["device"]["protection_state"] == "healthy"


def test_consumed_challenge_cannot_authorize_later_sequence(tmp_path):
    user_id, _security, device, gateway = _setup(tmp_path)
    issued = gateway.issue_challenge(user_id, device["id"])
    first = _heartbeat(device["id"], issued["challenge_nonce"], sequence=1)
    gateway.verify_and_record(
        user_id,
        first,
        signature_b64=_signature(first),
        signature_verifier=_verifier,
    )

    second = _heartbeat(device["id"], issued["challenge_nonce"], sequence=2)
    with pytest.raises(PermissionError, match="no longer pending"):
        gateway.verify_and_record(
            user_id,
            second,
            signature_b64=_signature(second),
            signature_verifier=_verifier,
        )


def test_new_challenge_supersedes_previous_pending_challenge(tmp_path):
    user_id, _security, device, gateway = _setup(tmp_path)
    old = gateway.issue_challenge(user_id, device["id"])
    new = gateway.issue_challenge(user_id, device["id"])

    old_heartbeat = _heartbeat(device["id"], old["challenge_nonce"])
    with pytest.raises(PermissionError, match="no longer pending"):
        gateway.verify_and_record(
            user_id,
            old_heartbeat,
            signature_b64=_signature(old_heartbeat),
            signature_verifier=_verifier,
        )

    new_heartbeat = _heartbeat(device["id"], new["challenge_nonce"])
    result = gateway.verify_and_record(
        user_id,
        new_heartbeat,
        signature_b64=_signature(new_heartbeat),
        signature_verifier=_verifier,
    )
    assert result["challenge_consumed"] is True


def test_expired_challenge_is_rejected_and_marked_expired(tmp_path):
    user_id, _security, device, gateway = _setup(tmp_path)
    issued = gateway.issue_challenge(user_id, device["id"])
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with gateway._connect() as con:
        con.execute(
            "UPDATE aura_sec_heartbeat_challenges SET expires_at=? WHERE id=?",
            (expired_at, issued["challenge_id"]),
        )

    heartbeat = _heartbeat(device["id"], issued["challenge_nonce"])
    with pytest.raises(PermissionError, match="expired"):
        gateway.verify_and_record(
            user_id,
            heartbeat,
            signature_b64=_signature(heartbeat),
            signature_verifier=_verifier,
        )

    with gateway._connect() as con:
        row = con.execute(
            "SELECT status FROM aura_sec_heartbeat_challenges WHERE id=?",
            (issued["challenge_id"],),
        ).fetchone()
    assert row["status"] == "expired"


def test_revoked_device_cannot_receive_new_heartbeat_challenge(tmp_path):
    user_id, security, device, gateway = _setup(tmp_path)
    security.revoke_device(user_id, device["id"])
    with pytest.raises(PermissionError, match="Revoked Aura Sec device"):
        gateway.issue_challenge(user_id, device["id"])
