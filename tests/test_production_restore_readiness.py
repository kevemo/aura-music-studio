from __future__ import annotations

import json
from datetime import datetime, timezone

from aura_music_studio.production_readiness import build_readiness_report


def _production_env() -> dict[str, str]:
    return {
        "AURA_DEPLOYMENT_ENV": "production",
        "LSS_PAYMENT_PROVIDER": "paypal",
        "LSS_PAYMENT_MODE": "verified_paypal_webhook",
        "LSS_PAYPAL_ENVIRONMENT": "live",
        "LSS_PAYPAL_CLIENT_ID": "paypal-client-0123456789",
        "LSS_PAYPAL_CLIENT_SECRET": "paypal-secret-0123456789",
        "LSS_PAYPAL_WEBHOOK_ID": "paypal-webhook-0123456789",
        "AURA_GPU_REQUIRED": "true",
        "AURA_REQUIRE_LIVE_RENDERER": "true",
        "AURA_ACESTEP_API_URL": "http://ace-step:8001",
        "ACESTEP_API_KEY": "ace-step-private-0123456789",
        "AURA_MONITORING_ENABLED": "true",
        "AURA_MONITORING_TOKEN": "monitoring-private-0123456789",
        "LSS_AUTO_BACKUP_ENABLED": "true",
        "LSS_AUTO_BACKUP_INTERVAL_HOURS": "24",
        "LSS_AUTO_BACKUP_KEEP": "7",
        "LSS_BACKUP_AGE_RECIPIENT": "age1exampleproductionrecipient",
        "LSS_PUBLIC_BASE_URL": "https://studio.example.test",
        "LSS_COOKIE_SECURE": "true",
        "LSS_PROVENANCE_SECRET": "provenance-private-0123456789",
        "LSS_ADMIN_KEY": "owner-private-0123456789",
        "AURA_WEB_ALLOW_HTTP": "false",
        "LSS_DB_PATH": "/srv/aura/data/studio.sqlite3",
        "AURA_PROJECTS_ROOT": "/srv/aura/projects",
        "LSS_BACKUP_DIR": "/srv/aura/backups",
    }


def _evidence(*, production_backup_used: bool, validation_source: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "restore_drill",
        "result": "verified",
        "environment": "recovery" if production_backup_used else "ci",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": 1.0,
        "backup_hashes_verified": True,
        "database_integrity": "ok",
        "application_data_check": True,
        "application_validation_source": validation_source,
        "production_backup_used": production_backup_used,
    }


def test_synthetic_restore_is_mechanism_evidence_not_production_release_evidence(tmp_path):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(_evidence(production_backup_used=False, validation_source="synthetic_probe")), encoding="utf-8")
    env = _production_env()
    env["LSS_RESTORE_EVIDENCE_PATH"] = str(path)

    report = build_readiness_report(env)
    restore = report["categories"]["restore_evidence"]

    assert report["configuration_ready"] is True
    assert restore["ok"] is False
    assert restore["details"]["mechanism_verified"] is True
    assert restore["details"]["verified"] is False
    assert restore["details"]["production_backup_used"] is False
    assert set(report["release_blocking_categories"]) == {"runtime_dependencies", "restore_evidence"}
    assert report["production_ready"] is False


def test_explicitly_validated_production_restore_satisfies_restore_gate_only(tmp_path):
    path = tmp_path / "production.json"
    path.write_text(json.dumps(_evidence(production_backup_used=True, validation_source="explicit_validator")), encoding="utf-8")
    env = _production_env()
    env["LSS_RESTORE_EVIDENCE_PATH"] = str(path)

    report = build_readiness_report(env)
    restore = report["categories"]["restore_evidence"]

    assert report["configuration_ready"] is True
    assert restore["ok"] is True
    assert restore["details"]["mechanism_verified"] is True
    assert restore["details"]["verified"] is True
    assert restore["details"]["production_backup_used"] is True
    assert restore["details"]["application_validation_source"] == "explicit_validator"
    assert report["release_blocking_categories"] == ["runtime_dependencies"]
    assert report["production_ready"] is False
