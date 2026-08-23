from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_music_studio.backup_scheduler import BackupScheduler
from aura_music_studio.studio_settings import StudioSettings


def test_backup_runtime_settings_override_environment_without_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "studio.sqlite3"))
    monkeypatch.setenv("AURA_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LSS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("LSS_BACKUP_STATUS", str(tmp_path / "backup-status.json"))
    monkeypatch.setenv("LSS_AUTO_BACKUP_ENABLED", "false")
    monkeypatch.setenv("LSS_AUTO_BACKUP_INTERVAL_HOURS", "24")

    scheduler = BackupScheduler()
    assert scheduler.enabled is False
    assert scheduler.interval_hours == 24

    settings = StudioSettings(scheduler.manager.store)
    settings.update_many({
        "auto_backup_enabled": True,
        "auto_backup_interval_hours": 6,
        "auto_backup_keep": 12,
        "auto_backup_include_outputs": True,
        "auto_backup_include_work": False,
    })

    # Same scheduler object re-reads SQLite settings on each property access.
    assert scheduler.enabled is True
    assert scheduler.interval_hours == 6
    assert scheduler.keep == 12
    assert scheduler.include_outputs is True
    assert scheduler.include_work is False


def test_backup_runtime_settings_reject_out_of_range_values(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "studio.sqlite3"))
    settings = StudioSettings()
    with pytest.raises(ValueError):
        settings.set("auto_backup_interval_hours", 0)
    with pytest.raises(ValueError):
        settings.set("auto_backup_keep", 1000)
    with pytest.raises(KeyError):
        settings.set("smtp_password", "must never be stored here")


def test_backup_status_contains_no_encryption_private_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "studio.sqlite3"))
    monkeypatch.setenv("AURA_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LSS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("LSS_BACKUP_STATUS", str(tmp_path / "backup-status.json"))
    monkeypatch.setenv("LSS_BACKUP_AGE_RECIPIENT", "age1publicrecipient")
    scheduler = BackupScheduler()
    scheduler._status(last_result={"ran": False, "reason": "test"})
    payload = json.loads(Path(monkeypatch.getenv("LSS_BACKUP_STATUS") if hasattr(monkeypatch, "getenv") else str(tmp_path / "backup-status.json")).read_text())
    text = json.dumps(payload)
    assert "age1publicrecipient" not in text
    assert "private" not in text.lower()
