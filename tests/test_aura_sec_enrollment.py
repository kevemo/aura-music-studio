from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_enrollment import AuraSecEnrollmentStore
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


def test_unverified_device_proof_cannot_consume_challenge(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    with pytest.raises(PermissionError, match="proof/attestation"):
        enrollment.complete_verified_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge=challenge["challenge"],
            proof_verified=False,
            public_key_fingerprint="a" * 64,
        )
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_wrong_challenge_is_rejected_without_consuming_real_one(tmp_path):
    _accounts, _security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    with pytest.raises(PermissionError, match="does not match"):
        enrollment.complete_verified_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge="wrong-challenge-value",
            proof_verified=True,
            public_key_fingerprint="b" * 64,
        )
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "pending"


def test_successful_verified_enrollment_starts_awaiting_heartbeat(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    result = enrollment.complete_verified_enrollment(
        user_id,
        challenge["challenge_id"],
        challenge=challenge["challenge"],
        proof_verified=True,
        public_key_fingerprint="c" * 64,
    )
    assert result["enrolled"] is True
    assert result["protection_state"] == "awaiting_heartbeat"
    assert result["device"]["protection_state"] == "awaiting_heartbeat"
    assert result["device"]["status"] == "enrolled"
    assert len(security.list_devices(user_id)) == 1
    stored = enrollment._challenge(user_id, challenge["challenge_id"])
    assert stored["status"] == "consumed"
    assert stored["consumed_at"]
    assert stored["device_id"] == result["device"]["id"]


def test_one_time_challenge_cannot_be_replayed(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Windows PC",
        platform="windows",
        architecture="x64",
    )
    enrollment.complete_verified_enrollment(
        user_id,
        challenge["challenge_id"],
        challenge=challenge["challenge"],
        proof_verified=True,
        public_key_fingerprint="d" * 64,
    )
    with pytest.raises(PermissionError, match="already been used"):
        enrollment.complete_verified_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge=challenge["challenge"],
            proof_verified=True,
            public_key_fingerprint="e" * 64,
        )
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
        enrollment.complete_verified_enrollment(
            user_id,
            challenge["challenge_id"],
            challenge=challenge["challenge"],
            proof_verified=True,
            public_key_fingerprint="f" * 64,
            now=expires + timedelta(seconds=1),
        )
    assert enrollment._challenge(user_id, challenge["challenge_id"])["status"] == "expired"


def test_device_limit_is_checked_before_creating_another_challenge(tmp_path):
    _accounts, security, enrollment, user_id = _stores(tmp_path, device_limit=1)
    challenge = enrollment.create_challenge(
        user_id,
        display_name="Device One",
        platform="windows",
        architecture="x64",
    )
    enrollment.complete_verified_enrollment(
        user_id,
        challenge["challenge_id"],
        challenge=challenge["challenge"],
        proof_verified=True,
        public_key_fingerprint="1" * 64,
    )
    assert len(security.list_devices(user_id)) == 1
    with pytest.raises(PermissionError, match="device limit"):
        enrollment.create_challenge(
            user_id,
            display_name="Device Two",
            platform="macos",
            architecture="arm64",
        )
