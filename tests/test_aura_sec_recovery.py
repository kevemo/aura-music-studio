from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_recovery import AuraSecRecoveryStore
from aura_music_studio.aura_sec_store import AuraSecStore


NOW = datetime(2026, 8, 27, 5, 0, tzinfo=timezone.utc)


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "aura-sec-recovery.sqlite3")
    signup = accounts.signup(
        "recovery.member@example.test",
        "Recovery Member",
        "secure-recovery-test-password",
        "free",
    )
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    security = AuraSecStore(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="security-test",
        payment_reference="recovery-payment-test",
        device_limit=1,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Recovery Test PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="a" * 64,
    )
    return accounts, security, AuraSecRecoveryStore(accounts, security), signup.user_id, device


def test_unverified_backup_connection_cannot_be_registered(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    with pytest.raises(PermissionError, match="connection must be verified"):
        recovery.register_verified_target(
            user_id,
            device["id"],
            target_type="immutable_cloud",
            display_name="Protected Vault",
            encrypted=True,
            isolated_or_immutable=True,
            provider_connection_verified=False,
        )


def test_backup_target_alone_is_not_proven_recovery(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Protected Vault",
        encrypted=True,
        isolated_or_immutable=True,
        provider_connection_verified=True,
    )
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "no_verified_recovery_point"
    assert result["restore_proven"] is False


def test_unverified_recovery_point_cannot_count_toward_readiness(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Protected Vault",
        encrypted=True,
        isolated_or_immutable=True,
        provider_connection_verified=True,
    )
    with pytest.raises(PermissionError, match="integrity must be verified"):
        recovery.record_verified_recovery_point(
            user_id,
            device["id"],
            target["id"],
            content_digest="b" * 64,
            manifest_digest="c" * 64,
            integrity_verified=False,
            malware_scan_state="clean",
            created_at=NOW,
        )


def test_clean_fresh_recovery_point_still_requires_restore_drill(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Protected Vault",
        encrypted=True,
        isolated_or_immutable=True,
        provider_connection_verified=True,
    )
    recovery.record_verified_recovery_point(
        user_id,
        device["id"],
        target["id"],
        content_digest="b" * 64,
        manifest_digest="c" * 64,
        integrity_verified=True,
        malware_scan_state="clean",
        created_at=NOW - timedelta(hours=1),
    )
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "restore_test_required"
    assert result["restore_proven"] is False


def test_successful_restore_drill_requires_digest_match(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Protected Vault",
        encrypted=True,
        isolated_or_immutable=True,
        provider_connection_verified=True,
    )
    point = recovery.record_verified_recovery_point(
        user_id,
        device["id"],
        target["id"],
        content_digest="d" * 64,
        manifest_digest="e" * 64,
        integrity_verified=True,
        malware_scan_state="clean",
        created_at=NOW - timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="does not match"):
        recovery.record_restore_drill(
            user_id,
            device["id"],
            point["id"],
            status="success",
            integrity_reverified=True,
            restored_content_digest="f" * 64,
            completed_at=NOW,
        )


def test_recent_verified_clean_restore_on_isolated_encrypted_target_is_ransomware_ready(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Protected Vault",
        encrypted=True,
        isolated_or_immutable=True,
        provider_connection_verified=True,
    )
    point = recovery.record_verified_recovery_point(
        user_id,
        device["id"],
        target["id"],
        content_digest="1" * 64,
        manifest_digest="2" * 64,
        integrity_verified=True,
        malware_scan_state="clean",
        created_at=NOW - timedelta(hours=1),
    )
    recovery.record_restore_drill(
        user_id,
        device["id"],
        point["id"],
        status="success",
        integrity_reverified=True,
        restored_content_digest="1" * 64,
        completed_at=NOW - timedelta(days=1),
    )
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "ransomware_ready"
    assert result["encrypted_target_present"] is True
    assert result["isolated_or_immutable_target_present"] is True
    assert result["restore_proven"] is True


def test_old_restore_drill_expires_from_readiness(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Protected Vault",
        encrypted=True,
        isolated_or_immutable=True,
        provider_connection_verified=True,
    )
    point = recovery.record_verified_recovery_point(
        user_id,
        device["id"],
        target["id"],
        content_digest="3" * 64,
        manifest_digest="4" * 64,
        integrity_verified=True,
        malware_scan_state="clean",
        created_at=NOW - timedelta(hours=2),
    )
    recovery.record_restore_drill(
        user_id,
        device["id"],
        point["id"],
        status="success",
        integrity_reverified=True,
        restored_content_digest="3" * 64,
        completed_at=NOW - timedelta(days=45),
    )
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "restore_test_required"
    assert result["restore_proven"] is False
