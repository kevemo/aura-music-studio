from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aura_music_studio import shared_sky_studio_history_graphics as history_mod
from aura_music_studio.shared_sky_studio_recovery_hardening import (
    install_history_recovery_versioning,
)


def make_db(tmp_path: Path) -> str:
    path = str(tmp_path / "recovery.sqlite3")
    now = "2026-09-05T00:00:00+00:00"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE shared_sky_projects(
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL
            );
            CREATE TABLE shared_sky_scenes(
                id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,layout_key TEXT NOT NULL DEFAULT 'solo',
                transition_key TEXT NOT NULL DEFAULT 'fade',transition_ms INTEGER NOT NULL DEFAULT 350,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE shared_sky_sources(
                id TEXT PRIMARY KEY,scene_id TEXT NOT NULL,project_id TEXT NOT NULL,user_id TEXT NOT NULL,
                source_type TEXT NOT NULL,name TEXT NOT NULL,config_json TEXT NOT NULL DEFAULT '{}',
                visible INTEGER NOT NULL DEFAULT 1,locked INTEGER NOT NULL DEFAULT 0,z_index INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                FOREIGN KEY(scene_id) REFERENCES shared_sky_scenes(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE shared_sky_studio_sessions(
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,broadcast_id TEXT,
                profile_key TEXT NOT NULL,preview_scene_id TEXT,programme_scene_id TEXT,
                programme_snapshot_json TEXT NOT NULL DEFAULT '{}',transition_state TEXT NOT NULL DEFAULT 'idle',
                transition_json TEXT NOT NULL DEFAULT '{}',autosave_state_json TEXT NOT NULL DEFAULT '{}',
                last_transport_state_json TEXT NOT NULL DEFAULT '{}',version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE shared_sky_studio_versions(
                session_id TEXT NOT NULL,version INTEGER NOT NULL,state_json TEXT NOT NULL,created_at TEXT NOT NULL,
                PRIMARY KEY(session_id,version),
                FOREIGN KEY(session_id) REFERENCES shared_sky_studio_sessions(id) ON DELETE CASCADE
            );
            """
        )
        con.execute("INSERT INTO shared_sky_projects VALUES(?,?,?)", ("p1", "u1", "Show"))
        con.execute(
            "INSERT INTO shared_sky_scenes VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("s1", "p1", "u1", "Opening", 0, "solo", "fade", 350, now, now),
        )
        config = json.dumps(
            {"privacy": "programme_safe", "transform": {"x": 0, "y": 0, "width": 1, "height": 1}}
        )
        con.execute(
            "INSERT INTO shared_sky_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("src1", "s1", "p1", "u1", "image", "Image", config, 1, 0, 0, now, now),
        )
        con.execute(
            "INSERT INTO shared_sky_studio_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sess", "u1", "p1", None, "landscape-1080", "s1", None, "{}", "idle", "{}", "{}", "{}", 1, now, now),
        )
    return path


def test_history_managed_edit_records_same_transaction_recovery_version(tmp_path):
    install_history_recovery_versioning()
    path = make_db(tmp_path)
    repo = history_mod.HistoryRepository(path)
    repo.batch_transform(
        "u1",
        "sess",
        history_mod.BatchTransformRequest(
            expected_version=1,
            items=[history_mod.BatchTransformItem(source_id="src1", transform={"x": 0.25})],
        ),
    )
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        version = con.execute(
            "SELECT version,state_json FROM shared_sky_studio_versions WHERE session_id='sess'"
        ).fetchone()
    assert version["version"] == 2
    state = json.loads(version["state_json"])
    assert state["version"] == 2
    assert state["autosave_state"]["reason"] == "batch_transform"


def test_failed_atomic_edit_rolls_back_graph_and_recovery_ledger(tmp_path):
    install_history_recovery_versioning()
    path = make_db(tmp_path)
    repo = history_mod.HistoryRepository(path)
    with pytest.raises(KeyError):
        repo.batch_transform(
            "u1",
            "sess",
            history_mod.BatchTransformRequest(
                expected_version=1,
                items=[
                    history_mod.BatchTransformItem(source_id="src1", transform={"x": 0.5}),
                    history_mod.BatchTransformItem(source_id="missing", transform={"x": 0.1}),
                ],
            ),
        )
    with sqlite3.connect(path) as con:
        row = con.execute(
            "SELECT config_json FROM shared_sky_sources WHERE id='src1'"
        ).fetchone()
        version_count = con.execute(
            "SELECT COUNT(*) FROM shared_sky_studio_versions WHERE session_id='sess'"
        ).fetchone()[0]
        session_version = con.execute(
            "SELECT version FROM shared_sky_studio_sessions WHERE id='sess'"
        ).fetchone()[0]
    assert json.loads(row[0])["transform"]["x"] == 0
    assert version_count == 0
    assert session_version == 1


def test_recovery_version_ledger_is_bounded_to_latest_fifty(tmp_path):
    install_history_recovery_versioning()
    path = make_db(tmp_path)
    repo = history_mod.HistoryRepository(path)
    version = 1
    for step in range(55):
        repo.batch_transform(
            "u1",
            "sess",
            history_mod.BatchTransformRequest(
                expected_version=version,
                items=[
                    history_mod.BatchTransformItem(
                        source_id="src1",
                        transform={"x": (step % 10) / 20.0},
                    )
                ],
            ),
        )
        version += 1
    with sqlite3.connect(path) as con:
        count, minimum, maximum = con.execute(
            "SELECT COUNT(*),MIN(version),MAX(version) FROM shared_sky_studio_versions WHERE session_id='sess'"
        ).fetchone()
    assert count == 50
    assert minimum == 7
    assert maximum == 56
