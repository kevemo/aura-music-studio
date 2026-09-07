from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_music_studio.backup_restore_evidence import (
    BackupRestoreEvidenceStore,
    REQUIRED_RESTORE_CHECKS,
    assess_backup_restore_release_gate,
)


def _checks(**overrides: bool) -> dict[str, bool]:
    values = {name: True for name in REQUIRED_RESTORE_CHECKS}
    values.update(overrides)
    return values


def _record_args(**overrides):
    values = {
        "drill_id": "restore-drill-20260831-001",
        "environment": "production",
        "backup_artifact_ref": "backup-20260831T120000Z",
        "backup_sha256": "a" * 64,
        "schema_revision": "schema-20260831-001",
        "restore_target_type": "isolated-production-clone",
        "backup_created_at": "2026-08-31T12:00:00Z",
        "restore_started_at": "2026-08-31T12:10:00Z",
        "restore_completed_at": "2026-08-31T12:14:00Z",
        "verification_completed_at": "2026-08-31T12:15:00Z",
        "checks": _checks(),
    }
    values.update(overrides)
    return values


def test_records_passed_drill_and_release_gate_ready(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")

    evidence = store.record(**_record_args())

    assert evidence["passed"] is True
    assert evidence["measured_rpo_seconds"] == 600
    assert evidence["measured_rto_seconds"] == 300
    assert len(evidence["evidence_sha256"]) == 64
    assert evidence["checks"] == _checks()

    gate = assess_backup_restore_release_gate(
        store=store,
        environment="production",
        max_evidence_age_seconds=600,
        max_rpo_seconds=600,
        max_rto_seconds=300,
        now=datetime(2026, 8, 31, 12, 20, tzinfo=timezone.utc),
    )
    assert gate["ready"] is True
    assert gate["reasons"] == []
    assert gate["drill_id"] == evidence["drill_id"]


def test_exact_retry_is_idempotent_but_changed_facts_fail_closed(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")
    first = store.record(**_record_args())
    second = store.record(**_record_args())

    assert second == first

    changed = _record_args(backup_sha256="b" * 64)
    with pytest.raises(ValueError, match="changed after it was recorded"):
        store.record(**changed)


def test_missing_required_check_is_rejected(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")
    checks = _checks()
    checks.pop("database_integrity_ok")

    with pytest.raises(ValueError, match="missing required checks"):
        store.record(**_record_args(checks=checks))


def test_required_checks_must_be_real_booleans(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")
    checks = _checks()
    checks["database_integrity_ok"] = 1  # type: ignore[assignment]

    with pytest.raises(ValueError, match="must be boolean"):
        store.record(**_record_args(checks=checks))


def test_latest_failed_drill_blocks_older_success(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")
    store.record(**_record_args(drill_id="restore-drill-older-pass"))
    store.record(
        **_record_args(
            drill_id="restore-drill-latest-fail",
            backup_artifact_ref="backup-20260831T130000Z",
            backup_sha256="b" * 64,
            backup_created_at="2026-08-31T13:00:00Z",
            restore_started_at="2026-08-31T13:05:00Z",
            restore_completed_at="2026-08-31T13:09:00Z",
            verification_completed_at="2026-08-31T13:10:00Z",
            checks=_checks(application_startup_ok=False),
        )
    )

    gate = assess_backup_restore_release_gate(
        store=store,
        environment="production",
        max_evidence_age_seconds=3600,
        max_rpo_seconds=600,
        max_rto_seconds=600,
        now=datetime(2026, 8, 31, 13, 15, tzinfo=timezone.utc),
    )

    assert gate["ready"] is False
    assert gate["drill_id"] == "restore-drill-latest-fail"
    assert "latest_restore_drill_failed_required_checks" in gate["reasons"]


def test_gate_reports_stale_rpo_and_rto_failures(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")
    store.record(
        **_record_args(
            drill_id="restore-drill-slow",
            backup_created_at="2026-08-31T10:00:00Z",
            restore_started_at="2026-08-31T10:20:00Z",
            restore_completed_at="2026-08-31T10:28:00Z",
            verification_completed_at="2026-08-31T10:30:00Z",
        )
    )

    gate = assess_backup_restore_release_gate(
        store=store,
        environment="production",
        max_evidence_age_seconds=300,
        max_rpo_seconds=300,
        max_rto_seconds=300,
        now=datetime(2026, 8, 31, 10, 40, tzinfo=timezone.utc),
    )

    assert gate["ready"] is False
    assert gate["measured_rpo_seconds"] == 1200
    assert gate["measured_rto_seconds"] == 600
    assert gate["reasons"] == [
        "restore_drill_evidence_is_stale",
        "measured_rpo_exceeds_release_threshold",
        "measured_rto_exceeds_release_threshold",
    ]


def test_secret_looking_identifier_is_rejected_before_persistence(tmp_path):
    db = tmp_path / "evidence.sqlite3"
    store = BackupRestoreEvidenceStore(db)
    secret = "_".join(("sk", "live", "SUPERSECRET0123456789"))

    with pytest.raises(ValueError, match="must not contain secrets"):
        store.record(**_record_args(backup_artifact_ref=secret))

    assert secret.encode("utf-8") not in db.read_bytes()


def test_invalid_hash_and_timestamp_order_are_rejected(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")

    with pytest.raises(ValueError, match="invalid backup SHA-256"):
        store.record(**_record_args(backup_sha256="not-a-hash"))

    with pytest.raises(ValueError, match="cannot be after restore start"):
        store.record(
            **_record_args(
                backup_created_at="2026-08-31T12:11:00Z",
                restore_started_at="2026-08-31T12:10:00Z",
            )
        )


def test_gate_is_false_without_evidence_and_rejects_implicit_thresholds(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")

    gate = assess_backup_restore_release_gate(
        store=store,
        environment="production",
        max_evidence_age_seconds=3600,
        max_rpo_seconds=600,
        max_rto_seconds=600,
        now=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
    )
    assert gate["ready"] is False
    assert gate["reasons"] == ["no_restore_drill_evidence"]

    with pytest.raises(ValueError, match="max_rto_seconds"):
        assess_backup_restore_release_gate(
            store=store,
            environment="production",
            max_evidence_age_seconds=3600,
            max_rpo_seconds=600,
            max_rto_seconds=-1,
        )


def test_future_evidence_timestamp_fails_release_gate(tmp_path):
    store = BackupRestoreEvidenceStore(tmp_path / "evidence.sqlite3")
    store.record(**_record_args())

    gate = assess_backup_restore_release_gate(
        store=store,
        environment="production",
        max_evidence_age_seconds=3600,
        max_rpo_seconds=600,
        max_rto_seconds=600,
        now=datetime(2026, 8, 31, 12, 14, 59, tzinfo=timezone.utc),
    )

    assert gate["ready"] is False
    assert "evidence_timestamp_is_in_future" in gate["reasons"]
