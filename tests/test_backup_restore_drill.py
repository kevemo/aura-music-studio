from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aura_music_studio.backup_drill import run_backup_restore_drill


def _source_state(root: Path) -> tuple[Path, Path]:
    database = root / "live" / "data" / "studio.sqlite3"
    database.parent.mkdir(parents=True)
    con = sqlite3.connect(database)
    try:
        con.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT INTO proof(value) VALUES (?)", ("restore-me",))
        con.commit()
    finally:
        con.close()

    projects = root / "live" / "projects"
    project = projects / "member-one" / "game-one"
    project.mkdir(parents=True)
    (project / "game.json").write_text('{"title":"Backup Drill"}', encoding="utf-8")
    (project / "asset.bin").write_bytes(b"verified-project-bytes")
    return database, projects


def test_backup_drill_restores_database_and_project_bytes_without_replacing_source(tmp_path):
    database, projects = _source_state(tmp_path)
    original_db = database.read_bytes()
    original_asset = (projects / "member-one" / "game-one" / "asset.bin").read_bytes()
    working = tmp_path / "isolated-drill"

    report = run_backup_restore_drill(database=database, projects=projects, working_dir=working)

    assert report["ok"] is True
    assert report["restored"] is True
    assert report["restored_database_integrity"] == "ok"
    assert report["verified_files"] >= 3
    assert report["project_file_count"] == 2
    assert report["deployment_secrets_changed"] is False
    assert report["source_state_replaced"] is False
    assert database.read_bytes() == original_db
    assert (projects / "member-one" / "game-one" / "asset.bin").read_bytes() == original_asset

    restored_db = Path(report["restored_database"])
    con = sqlite3.connect(restored_db)
    try:
        assert con.execute("SELECT value FROM proof").fetchone()[0] == "restore-me"
    finally:
        con.close()
    assert (Path(report["restored_projects"]) / "member-one" / "game-one" / "asset.bin").read_bytes() == original_asset


def test_backup_drill_refuses_live_tree_as_restore_working_directory(tmp_path):
    database, projects = _source_state(tmp_path)
    with pytest.raises(ValueError):
        run_backup_restore_drill(database=database, projects=projects, working_dir=database.parent)


def test_backup_drill_with_temporary_destination_does_not_return_stale_paths(tmp_path):
    database, projects = _source_state(tmp_path)
    report = run_backup_restore_drill(database=database, projects=projects)
    assert report["ok"] is True
    assert report["working_directory"] is None
    assert report["restored_database"] is None
    assert report["restored_projects"] is None
