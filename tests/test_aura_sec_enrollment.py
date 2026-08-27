from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_enrollment import (
    AuraSecEnrollmentStore,
    VerifiedEnrollmentProof,
)
from aura_music_studio.aura_sec_store import AuraSecStore


def _stores(tmp_path, *, licensed: bool = True, device_limit: int = 2):
    accounts = AccountStore(tmp_path / "aura-sec-enrollment.sqlite3")
    signup = accounts.signup(
        "enrollment.member@example.test",
        "Enrollment Member",
        "secure-enrollment-test-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    if licensed:
        security.activate_verified_purchase(
            signup.user_id,
            sku_id="security-test",
            payment_reference="enrollment-payment-test",
            device_limit=device_limit,
            period_days=31,
            verified_by="test-verifier",
        )
    return accounts, security, AuraSecEnrollmentStore(accounts, security), signup.user_id


def _verifier(*, fingerprint: str = "c" * 64, platform: str | None = None, architecture: str | None = None):
    def verify(payload, context):
        if payload.get("signed_challenge") != context.challenge:
            return None
        return VerifiedEnrollmentProof(
            public_key_fingerprint=fingerprint,
            proof_type="platform_attestation",
            verifier_id="test-native-attestation-verifier",
            evidence_digest=hashlib.sha256(
                f"{context.challenge_id}|{context.challenge}|verified".encode("utf-8")
            ).hexdigest(),
            platform=platform or context.platform,
            architecture=architecture or context.architecture,
            key_algorithm="p256",
            hardware_backed=True,
        )

    return verify


def _complete(enrollment, user_id, challenge, *, verifier=None, proof_payload=None, now=None):
    return enrollment.verify_and_complete_enrollment(
        user_id,
        challenge["challenge_id"],
        challenge=challenge["challenge"],
        proof_payload=proof_payload or {"signed_challenge": challenge["challenge"]},
        verifier=verifier or _verifier(),
        now=now,
    )


def test_challenge_requires_separate_active_security_licence(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path, licensed=False)
    with pytest.raises(PermissionError, match="Active Aura Sec licence"):
        enrollment.create_challenge(
            user_id,
            display_name="Windows PC",
            platform="windows",
            architecture="x64",
        )


def test_plaintext_challenge_is_returned_once_but_only_hash_is_persisted(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    stored = enrollment._challenge(user_id, challenge["challenge_id"])
    assert challenge["challenge"]
    assert "challenge" not in stored
    assert stored["challenge_hash"] != challenge["challenge"]
    assert len(stored["challenge_hash"]) == 64
    assert challenge["one_time"] is True
    assert challenge["browser_can_self_verify"] is False


def test_enrollment_has_no_boolean_verification_escape_hatch(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    assert not hasattr(enrollment, "complete_verified_enrollment")
    with pytest.raises(PermissionError, match="trusted Aura Sec native enrollment verifier"):
        enrollment.verify_and_complete_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge=challenge["challenge"],
            proof_payload={"proof_verified": True},
            verifier=None,
        )
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_wrong_challenge_is_rejected_before_verifier_is_called(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    called = False

    def verifier(_payload, _context):
        nonlocal called
        called = True
        return None

    with pytest.raises(PermissionError, match="does not match"):
        enrollment.verify_and_complete_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge="wrong-challenge-value",
            proof_payload={},
            verifier=verifier,
        )
    assert called is False
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_verifier_rejection_cannot_consume_challenge(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    with pytest.raises(PermissionError, match="proof/attestation"):
        enrollment.verify_and_complete_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge=challenge["challenge"],
            proof_payload={"signed_challenge": "invalid"},
            verifier=_verifier(),
        )
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_verified_proof_must_match_requested_platform_and_architecture(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    with pytest.raises(PermissionError, match="platform/architecture"):
        _complete(enrollment, user_id, challenge, verifier=_verifier(platform="macos", architecture="arm64"))
    assert len(security.list_devices(user_id)) == 0
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_verified_proof_requires_sha256_key_and_evidence_digests(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    with pytest.raises(PermissionError, match="public-key fingerprint"):
        _complete(enrollment, user_id, challenge, verifier=_verifier(fingerprint="not-a-sha256"))
    assert len(security.list_devices(user_id)) == 0
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_successful_verified_enrollment_starts_awaiting_heartbeat_and_audits_proof(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    result = _complete(enrollment, user_id, challenge)
    assert result["enrolled"] is True
    assert result["protection_state"] == "awaiting_heartbeat"
    assert result["device"]["protection_state"] == "awaiting_heartbeat"
    assert result["device"]["status"] == "enrolled"
    assert result["proof"]["type"] == "platform_attestation"
    assert result["proof"]["key_algorithm"] == "p256"
    assert result["proof"]["hardware_backed"] is True
    assert len(security.list_devices(user_id)) == 1

    stored = enrollment._challenge(user_id, challenge["challenge_id"])
    assert stored["status"] == "consumed"
    assert stored["consumed_at"]
    assert stored["device_id"] == result["device"]["id"]
    assert stored["verifier_id"] == "test-native-attestation-verifier"
    assert stored["proof_type"] == "platform_attestation"
    assert len(stored["evidence_digest"]) == 64
    assert stored["key_algorithm"] == "p256"
    assert stored["hardware_backed"] is True
    assert "signed_challenge" not in stored


def test_one_time_challenge_cannot_be_replayed(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    _complete(enrollment, user_id, challenge)
    with pytest.raises(PermissionError, match="already been used"):
        _complete(enrollment, user_id, challenge, verifier=_verifier(fingerprint="e" * 64))
    assert len(security.list_devices(user_id)) == 1


def test_expired_challenge_fails_closed_and_is_marked_expired(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
        ttl_minutes=1,
    )
    expires = datetime.fromisoformat(challenge["expires_at"]).astimezone(timezone.utc)
    with pytest.raises(PermissionError, match="expired"):
        _complete(enrollment, user_id, challenge, now=expires + timedelta(seconds=1))
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "expired"


def test_device_limit_is_checked_before_creating_another_challenge(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path, device_limit=1)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Device One",
        platform="windows",
        architecture="x64",
    )
    _complete(enrollment, user_id, challenge, verifier=_verifier(fingerprint="1" * 64))
    assert len(security.list_devices(user_id)) == 1
    with pytest.raises(PermissionError, match="device limit"):
        enrollment.create_challenge(
            user_id,
            display_name="Device Two",
            platform="macos",
            architecture="arm64",
        )
