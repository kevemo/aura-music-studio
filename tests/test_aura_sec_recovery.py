from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_recovery import (
    AuraSecRecoveryStore,
    VerifiedBackupTargetConnection,
    VerifiedRecoveryPointEvidence,
    VerifiedRestoreDrillEvidence,
)
from aura_music_studio.aura_sec_store import AuraSecStore


NOW = datetime(2026, 8, 27, 5, 0, tzinfo=timezone.utc)
DEVICE_FP = "a" * 64
SIGNATURE = base64.b64encode(b"s" * 64).decode("ascii")


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
        public_key_fingerprint=DEVICE_FP,
    )
    return accounts, security, AuraSecRecoveryStore(accounts, security), signup.user_id, device


def _target_verifier(
    *,
    encrypted=True,
    isolated=True,
    target_identity_digest="7" * 64,
    proof_type="provider_connection",
    digest_override=None,
):
    def verify(payload, context):
        if payload.get("provider_connected") is not True:
            return None
        digest = digest_override or hashlib.sha256(
            context.evidence_payload(
                target_identity_digest=target_identity_digest,
                proof_type=proof_type,
                encrypted=encrypted,
                isolated_or_immutable=isolated,
            )
        ).hexdigest()
        return VerifiedBackupTargetConnection(
            target_identity_digest=target_identity_digest,
            proof_type=proof_type,
            verifier_id="test-target-verifier",
            evidence_digest=digest,
            encrypted=encrypted,
            isolated_or_immutable=isolated,
        )

    return verify


def _register(recovery, user_id, device, *, verifier=None, target_type="immutable_cloud"):
    return recovery.register_verified_target(
        user_id,
        device["id"],
        target_type=target_type,
        display_name="Protected Vault",
        verification_payload={"provider_connected": True},
        verifier=verifier or _target_verifier(),
    )


def _point_verifier(*, scan_state="clean", fingerprint=DEVICE_FP, digest_override=None):
    def verify(expected_fingerprint, payload, signature):
        assert expected_fingerprint == DEVICE_FP
        assert signature == b"s" * 64
        return VerifiedRecoveryPointEvidence(
            public_key_fingerprint=fingerprint,
            verifier_id="test-recovery-point-verifier",
            key_algorithm="ed25519",
            evidence_digest=digest_override or hashlib.sha256(payload).hexdigest(),
            malware_scan_state=scan_state,
        )

    return verify


def _record_point(
    recovery,
    user_id,
    device,
    target,
    *,
    content_digest="b" * 64,
    manifest_digest="c" * 64,
    scan_state="clean",
    created_at=None,
    verifier=None,
):
    return recovery.record_verified_recovery_point(
        user_id,
        device["id"],
        target["id"],
        content_digest=content_digest,
        manifest_digest=manifest_digest,
        malware_scan_state=scan_state,
        signature_b64=SIGNATURE,
        verifier=verifier or _point_verifier(scan_state=scan_state),
        created_at=created_at,
    )


def _drill_verifier(
    *,
    status="success",
    integrity_reverified=True,
    restored_content_digest=None,
    fingerprint=DEVICE_FP,
    digest_override=None,
):
    def verify(expected_fingerprint, payload, signature):
        assert expected_fingerprint == DEVICE_FP
        assert signature == b"s" * 64
        return VerifiedRestoreDrillEvidence(
            public_key_fingerprint=fingerprint,
            verifier_id="test-restore-drill-verifier",
            key_algorithm="p256",
            evidence_digest=digest_override or hashlib.sha256(payload).hexdigest(),
            status=status,
            integrity_reverified=integrity_reverified,
            restored_content_digest=restored_content_digest,
        )

    return verify


def test_backup_target_connection_requires_structured_verifier_evidence(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    with pytest.raises(PermissionError, match="trusted Aura Sec backup target verifier"):
        recovery.register_verified_target(
            user_id,
            device["id"],
            target_type="immutable_cloud",
            display_name="Protected Vault",
            verification_payload={"provider_connection_verified": True},
            verifier=None,
        )
    with pytest.raises(PermissionError, match="immutability/isolation"):
        _register(recovery, user_id, device, verifier=_target_verifier(isolated=False))
    with pytest.raises(PermissionError, match="canonical payload"):
        _register(recovery, user_id, device, verifier=_target_verifier(digest_override="d" * 64))


def test_backup_target_properties_come_from_verifier_and_alone_are_not_proven_recovery(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = _register(recovery, user_id, device)
    assert target["encrypted"] is True
    assert target["isolated_or_immutable"] is True
    assert target["connection_verifier_id"] == "test-target-verifier"
    assert target["connection_proof_type"] == "provider_connection"
    assert target["target_identity_digest"] == "7" * 64
    assert len(target["connection_evidence_digest"]) == 64
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "no_verified_recovery_point"
    assert result["restore_proven"] is False


def test_recovery_point_integrity_requires_current_device_key_verifier(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = _register(recovery, user_id, device)
    with pytest.raises(PermissionError, match="trusted Aura Sec recovery-point verifier"):
        recovery.record_verified_recovery_point(
            user_id,
            device["id"],
            target["id"],
            content_digest="b" * 64,
            manifest_digest="c" * 64,
            malware_scan_state="clean",
            signature_b64=SIGNATURE,
            verifier=None,
            created_at=NOW,
        )
    with pytest.raises(PermissionError, match="does not match enrolled device"):
        _record_point(
            recovery,
            user_id,
            device,
            target,
            verifier=_point_verifier(fingerprint="f" * 64),
            created_at=NOW,
        )
    with pytest.raises(PermissionError, match="canonical payload"):
        _record_point(
            recovery,
            user_id,
            device,
            target,
            verifier=_point_verifier(digest_override="e" * 64),
            created_at=NOW,
        )


def test_clean_fresh_recovery_point_still_requires_restore_drill(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = _register(recovery, user_id, device)
    point = _record_point(
        recovery,
        user_id,
        device,
        target,
        created_at=NOW - timedelta(hours=1),
    )
    assert point["integrity_verified"] is True
    assert point["integrity_verifier_id"] == "test-recovery-point-verifier"
    assert point["integrity_key_algorithm"] == "ed25519"
    assert len(point["integrity_evidence_digest"]) == 64
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "restore_test_required"
    assert result["restore_proven"] is False


def test_successful_restore_drill_requires_signed_integrity_and_digest_match(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = _register(recovery, user_id, device)
    point = _record_point(
        recovery,
        user_id,
        device,
        target,
        content_digest="d" * 64,
        manifest_digest="e" * 64,
        created_at=NOW - timedelta(hours=1),
    )
    with pytest.raises(PermissionError, match="post-restore integrity"):
        recovery.record_restore_drill(
            user_id,
            device["id"],
            point["id"],
            status="success",
            restored_content_digest="d" * 64,
            signature_b64=SIGNATURE,
            verifier=_drill_verifier(
                integrity_reverified=False,
                restored_content_digest="d" * 64,
            ),
            completed_at=NOW,
        )
    with pytest.raises(PermissionError, match="does not match recovery point"):
        recovery.record_restore_drill(
            user_id,
            device["id"],
            point["id"],
            status="success",
            restored_content_digest="f" * 64,
            signature_b64=SIGNATURE,
            verifier=_drill_verifier(restored_content_digest="f" * 64),
            completed_at=NOW,
        )


def test_recent_verified_clean_restore_on_isolated_encrypted_target_is_ransomware_ready(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = _register(recovery, user_id, device)
    point = _record_point(
        recovery,
        user_id,
        device,
        target,
        content_digest="1" * 64,
        manifest_digest="2" * 64,
        created_at=NOW - timedelta(hours=1),
    )
    drill = recovery.record_restore_drill(
        user_id,
        device["id"],
        point["id"],
        status="success",
        restored_content_digest="1" * 64,
        signature_b64=SIGNATURE,
        verifier=_drill_verifier(restored_content_digest="1" * 64),
        completed_at=NOW - timedelta(days=1),
    )
    assert drill["integrity_reverified"] is True
    assert drill["verifier_id"] == "test-restore-drill-verifier"
    assert drill["verifier_key_algorithm"] == "p256"
    assert len(drill["verifier_evidence_digest"]) == 64
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "ransomware_ready"
    assert result["encrypted_target_present"] is True
    assert result["isolated_or_immutable_target_present"] is True
    assert result["restore_proven"] is True


def test_old_restore_drill_expires_from_readiness(tmp_path):
    _accounts, _security, recovery, user_id, device = _stores(tmp_path)
    target = _register(recovery, user_id, device)
    point = _record_point(
        recovery,
        user_id,
        device,
        target,
        content_digest="3" * 64,
        manifest_digest="4" * 64,
        created_at=NOW - timedelta(hours=2),
    )
    recovery.record_restore_drill(
        user_id,
        device["id"],
        point["id"],
        status="success",
        restored_content_digest="3" * 64,
        signature_b64=SIGNATURE,
        verifier=_drill_verifier(restored_content_digest="3" * 64),
        completed_at=NOW - timedelta(days=45),
    )
    result = recovery.readiness(user_id, device["id"], now=NOW)
    assert result["state"] == "restore_test_required"
    assert result["restore_proven"] is False


def test_revoked_device_cannot_register_or_report_recovery_evidence(tmp_path):
    _accounts, security, recovery, user_id, device = _stores(tmp_path)
    security.revoke_device(user_id, device["id"])
    with pytest.raises(PermissionError, match="revoked device"):
        _register(recovery, user_id, device)
