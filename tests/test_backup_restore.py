from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from aura_music_studio.backup import StudioBackupManager


def _database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)")
        con.execute("INSERT INTO users VALUES ('u1','member@example.com')")
        con.commit()
    finally:
        con.close()


def test_backup_contains_db_projects_manifest_but_not_environment(tmp_path):
    db = tmp_path / "data" / "studio.sqlite3"
    projects = tmp_path / "projects"
    project = projects / "user1" / "song1"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text("title: Test\n", encoding="utf-8")
    (project / "master.wav").write_bytes(b"RIFF-real-audio-placeholder")
    # Even if a deployment-looking .env exists outside projects, it must not enter the archive.
    (tmp_path / ".env").write_text("SECRET=never-back-this-up\n", encoding="utf-8")
    _database(db)

    manager = StudioBackupManager(database=db, projects=projects, backup_dir=tmp_path / "backups")
    result = manager.create()
    archive = Path(result["backup"])
    inspected = StudioBackupManager.inspect(archive)

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "database/live_sound_studio.sqlite3" in names
        assert "projects/user1/song1/project.yaml" in names
        assert "backup-manifest.json" in names
        assert not any(name.endswith(".env") for name in names)
        manifest = json.loads(zf.read("backup-manifest.json"))
        assert manifest["secrets_included"] is False
        assert manifest["environment_included"] is False
    assert inspected["verified_files"] >= 3


def test_restore_refuses_without_explicit_offline_confirmation(tmp_path):
    db = tmp_path / "db.sqlite3"
    projects = tmp_path / "projects"
    projects.mkdir()
    _database(db)
    manager = StudioBackupManager(database=db, projects=projects, backup_dir=tmp_path / "backups")
    archive = Path(manager.create()["backup"])
    with pytest.raises(PermissionError):
        manager.restore(archive, confirm_offline=False)


def test_verified_backup_restores_to_new_machine_paths(tmp_path):
    src_db = tmp_path / "source" / "studio.sqlite3"
    src_projects = tmp_path / "source" / "projects"
    song = src_projects / "tenant" / "song"
    song.mkdir(parents=True)
    (song / "aura_session.json").write_text('{"name":"Song"}', encoding="utf-8")
    _database(src_db)
    source = StudioBackupManager(database=src_db, projects=src_projects, backup_dir=tmp_path / "backups")
    archive = Path(source.create()["backup"])

    dest_db = tmp_path / "new-host" / "data" / "studio.sqlite3"
    dest_projects = tmp_path / "new-host" / "projects"
    target = StudioBackupManager(database=dest_db, projects=dest_projects, backup_dir=tmp_path / "new-backups")
    restored = target.restore(archive, confirm_offline=True)
    assert restored["restored"] is True
    assert (dest_projects / "tenant" / "song" / "aura_session.json").is_file()
    con = sqlite3.connect(dest_db)
    try:
        assert con.execute("SELECT email FROM users WHERE id='u1'").fetchone()[0] == "member@example.com"
    finally:
        con.close()


def test_inspect_rejects_path_traversal_archive(tmp_path):
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "bad")
        zf.writestr("backup-manifest.json", json.dumps({
            "format_version": 1,
            "secrets_included": False,
            "environment_included": False,
            "files": [],
        }))
    with pytest.raises(ValueError):
        StudioBackupManager.inspect(archive)
