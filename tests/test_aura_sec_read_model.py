from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_sec_read_model import AuraSecReadModel
from aura_music_studio.aura_sec_recovery import AuraSecRecoveryStore
from aura_music_studio.aura_sec_store import AuraSecStore


def _fixture(tmp_path):
    accounts = AccountStore(tmp_path / "read-model.sqlite3")
    signup = accounts.signup("read.model@example.test", "Read Model", "secure-test-password", "free")
    accounts.decide_membership(signup.approval_token, "approve", "owner")
    security = AuraSecStore(accounts)
    recovery = AuraSecRecoveryStore(accounts, security)
    read = AuraSecReadModel(accounts)
    security.activate_verified_purchase(
        signup.user_id,
        sku_id="test-security-sku",
        payment_reference="read-model-payment",
        device_limit=2,
        period_days=31,
        verified_by="test-verifier",
    )
    device = security.enroll_attested_device(
        signup.user_id,
        display_name="Read Model PC",
        platform="windows",
        architecture="x64",
        public_key_fingerprint="a" * 64,
    )
    return accounts, security, recovery, read, signup.user_id, device


def test_read_model_projects_verified_incidents_actions_and_recovery(tmp_path):
    _accounts, security, recovery, read, user_id, device = _fixture(tmp_path)
    incident = security.create_incident(
        user_id,
        device["id"],
        severity="high",
        title="Verified test incident",
        detection_id="AURA-TEST-1",
        confidence=0.91,
        summary={"source": "verified-test"},
    )
    security.propose_action(
        user_id,
        device["id"],
        incident_id=incident["id"],
        action_type="isolate_network",
        risk_class="confirmation_required",
        details={"reason": "containment test"},
    )
    target = recovery.register_verified_target(
        user_id,
        device["id"],
        target_type="immutable_cloud",
        display_name="Immutable Test Vault",
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
    )

    incidents = read.incidents(user_id)
    actions = read.actions(user_id)
    targets = read.backup_targets(user_id)
    points = read.recovery_points(user_id)
    counts = read.counts(user_id)

    assert incidents[0]["title"] == "Verified test incident"
    assert incidents[0]["summary"]["source"] == "verified-test"
    assert actions[0]["action_type"] == "isolate_network"
    assert actions[0]["details"]["reason"] == "containment test"
    assert targets[0]["encrypted"] is True
    assert targets[0]["isolated_or_immutable"] is True
    assert points[0]["integrity_verified"] is True
    assert counts["incidents"]["open"] == 1
    assert counts["incidents"]["urgent"] == 1
    assert counts["actions"]["awaiting_approval"] == 1
    assert counts["recovery"]["isolated_targets"] == 1


def test_read_model_is_user_scoped(tmp_path):
    accounts, security, _recovery, read, user_id, device = _fixture(tmp_path)
    other = accounts.signup("other.read@example.test", "Other", "secure-test-password", "free")
    accounts.decide_membership(other.approval_token, "approve", "owner")
    security.create_incident(
        user_id,
        device["id"],
        severity="medium",
        title="User one only",
    )
    assert len(read.incidents(user_id)) == 1
    assert read.incidents(other.user_id) == []
    assert read.counts(other.user_id)["incidents"]["total"] == 0
