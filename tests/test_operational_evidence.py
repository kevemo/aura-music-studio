from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from aura_music_studio.operational_evidence import (
    load_restore_evidence,
    probe_runtime_storage,
    run_restore_drill,
    write_restore_evidence,
)


def test_synthetic_restore_drill_is_verified_but_never_claims_production_backup(tmp_path):
    evidence = run_restore_drill(tmp_path, environment="ci")
    assert evidence["result"] == "verified"
    assert evidence["database_integrity"] == "ok"
    assert evidence["application_data_check"] is True
    assert evidence["backup_hashes_verified"] is True
    assert evidence["production_backup_used"] is False
    assert evidence["deployment_secrets_changed"] is False

    output = write_restore_evidence(tmp_path / "evidence.json", evidence)
    loaded = load_restore_evidence(output)
    assert loaded["verified"] is True
    assert loaded["environment"] == "ci"
    assert loaded["production_backup_used"] is False


def test_restore_evidence_fails_closed_when_missing_invalid_or_stale(tmp_path):
    assert load_restore_evidence(None)["state"] == "not_configured"
    assert load_restore_evidence(tmp_path / "missing.json")["state"] == "missing"

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    assert load_restore_evidence(invalid)["state"] == "invalid"

    stale = {
        "schema_version": 1,
        "kind": "restore_drill",
        "result": "verified",
        "environment": "staging",
        "executed_at": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
        "backup_hashes_verified": True,
        "database_integrity": "ok",
        "application_data_check": True,
        "production_backup_used": True,
    }
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    loaded = load_restore_evidence(stale_path, max_age_hours=24)
    assert loaded["verified"] is False
    assert loaded["state"] == "unverified"


def test_runtime_storage_probe_is_bounded_non_destructive_connectivity_only(tmp_path):
    db = tmp_path / "data" / "studio.sqlite3"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        con.commit()
    finally:
        con.close()
    projects = tmp_path / "projects"
    backups = tmp_path / "backups"
    projects.mkdir()
    backups.mkdir()

    result = probe_runtime_storage(
        {
            "LSS_DB_PATH": str(db),
            "AURA_PROJECTS_ROOT": str(projects),
            "LSS_BACKUP_DIR": str(backups),
        }
    )
    assert result["verified"] is True
    assert result["database"]["state"] == "healthy"
    assert result["database"]["connectivity_check"] == "ok"
    assert result["database"]["full_integrity_check_performed"] is False
    assert result["project_storage"]["state"] == "healthy"
    assert result["backup_storage"]["state"] == "healthy"
    assert result["external_provider_probes_performed"] is False
    assert result["destructive_writes_performed"] is False
