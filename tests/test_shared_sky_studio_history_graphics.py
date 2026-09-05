from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aura_music_studio import shared_sky_studio_history_graphics as mod
from aura_music_studio.shared_sky_control_room import StudioConflict, StudioInvariantError, StudioRepository


def make_repo(tmp_path: Path, monkeypatch) -> tuple[mod.HistoryRepository, str]:
    path = str(tmp_path / "history.sqlite3")
    now = "2026-09-05T00:00:00+00:00"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE shared_sky_projects(
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
                workspace_mode TEXT NOT NULL DEFAULT 'professional',canvas_mode TEXT NOT NULL DEFAULT 'dual',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL
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
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(user_id,project_id),
                FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE shared_sky_studio_versions(
                session_id TEXT NOT NULL,version INTEGER NOT NULL,state_json TEXT NOT NULL,created_at TEXT NOT NULL,
                PRIMARY KEY(session_id,version),FOREIGN KEY(session_id) REFERENCES shared_sky_studio_sessions(id) ON DELETE CASCADE
            );
            """
        )
        con.execute(
            "INSERT INTO shared_sky_projects VALUES(?,?,?,?,?,?,?,?)",
            ("p1", "u1", "Show", "", "professional", "dual", now, now),
        )
        con.executemany(
            "INSERT INTO shared_sky_scenes VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                ("s1", "p1", "u1", "Opening", 0, "solo", "fade", 350, now, now),
                ("s2", "p1", "u1", "Interview", 1, "interview", "fade", 350, now, now),
            ],
        )
        base_config = json.dumps(
            {"privacy": "programme_safe", "transform": {"x": 0, "y": 0, "width": 1, "height": 1}}
        )
        con.executemany(
            "INSERT INTO shared_sky_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("src1", "s1", "p1", "u1", "image", "One", base_config, 1, 0, 0, now, now),
                ("src2", "s1", "p1", "u1", "text", "Two", base_config, 1, 0, 1, now, now),
            ],
        )
        programme = json.dumps({"scene": {"id": "s1"}, "sources": [{"id": "committed"}]})
        con.execute(
            "INSERT INTO shared_sky_studio_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sess", "u1", "p1", None, "landscape-1080", "s1", "s1", programme, "idle", "{}", "{}", "{}", 1, now, now,
            ),
        )
    history = mod.HistoryRepository(path)
    core_repo = StudioRepository(path)
    monkeypatch.setattr(mod, "studio_repo", core_repo)
    return history, path


def read_source(path: str, source_id: str) -> dict:
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM shared_sky_sources WHERE id=?", (source_id,)).fetchone()
    item = dict(row)
    item["config"] = json.loads(item.pop("config_json"))
    return item


def read_session(path: str) -> dict:
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM shared_sky_studio_sessions WHERE id='sess'").fetchone()
    return dict(row)


def test_graphic_style_is_bounded_and_rejects_css_injection():
    style = mod.normalize_graphic_style(
        {"font_size": 999, "font_weight": 5, "background_opacity": 4, "text_color": "#AABBCC"}
    )
    assert style["font_size"] == 200
    assert style["font_weight"] == 100
    assert style["background_opacity"] == 1.0
    assert style["text_color"] == "#aabbcc"
    with pytest.raises(StudioInvariantError, match="six-digit hex"):
        mod.normalize_graphic_style({"text_color": "red;position:fixed"})


def test_multi_source_transform_is_one_atomic_undo_redo_action(tmp_path, monkeypatch):
    history, path = make_repo(tmp_path, monkeypatch)
    history.batch_transform(
        "u1",
        "sess",
        mod.BatchTransformRequest(
            expected_version=1,
            items=[
                mod.BatchTransformItem(source_id="src1", transform={"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.4}),
                mod.BatchTransformItem(source_id="src2", transform={"x": 0.5, "y": 0.2, "width": 0.4, "height": 0.4}),
            ],
        ),
    )
    assert read_session(path)["version"] == 2
    assert read_source(path, "src1")["config"]["transform"]["x"] == pytest.approx(0.1)
    assert read_source(path, "src2")["config"]["transform"]["x"] == pytest.approx(0.5)
    state = history.state("u1", "sess")
    assert state["undo_depth"] == 1 and state["undo_action"] == "source_transform"

    programme_before = read_session(path)["programme_snapshot_json"]
    assert history.undo("u1", "sess", 2) == "source_transform"
    assert read_source(path, "src1")["config"]["transform"]["x"] == 0
    assert read_source(path, "src2")["config"]["transform"]["x"] == 0
    assert read_session(path)["programme_snapshot_json"] == programme_before
    assert history.state("u1", "sess")["can_redo"] is True

    assert history.redo("u1", "sess", 3) == "source_transform"
    assert read_source(path, "src1")["config"]["transform"]["x"] == pytest.approx(0.1)
    assert read_source(path, "src2")["config"]["transform"]["x"] == pytest.approx(0.5)
    assert read_session(path)["programme_snapshot_json"] == programme_before


def test_new_action_after_undo_clears_redo_stack(tmp_path, monkeypatch):
    history, _ = make_repo(tmp_path, monkeypatch)
    history.batch_transform(
        "u1",
        "sess",
        mod.BatchTransformRequest(
            expected_version=1,
            items=[mod.BatchTransformItem(source_id="src1", transform={"x": 0.2})],
        ),
    )
    history.undo("u1", "sess", 2)
    history.create_source(
        "u1",
        "sess",
        mod.TrackedSourceCreate(
            source_type="text",
            name="Fresh",
            config={"text": "Fresh"},
            expected_version=3,
        ),
    )
    state = history.state("u1", "sess")
    assert state["can_redo"] is False
    assert state["undo_action"] == "source_create"


def test_delete_source_undo_restores_same_stable_id(tmp_path, monkeypatch):
    history, path = make_repo(tmp_path, monkeypatch)
    history.delete_sources(
        "u1",
        "sess",
        mod.BatchDeleteRequest(source_ids=["src1"], expected_version=1),
    )
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT 1 FROM shared_sky_sources WHERE id='src1'").fetchone() is None
    history.undo("u1", "sess", 2)
    assert read_source(path, "src1")["id"] == "src1"


def test_scene_lock_blocks_content_edit_until_unlocked(tmp_path, monkeypatch):
    history, _ = make_repo(tmp_path, monkeypatch)
    history.patch_scene(
        "u1", "sess", "s1", mod.ScenePatchTracked(locked=True, expected_version=1)
    )
    with pytest.raises(StudioInvariantError, match="Unlock this scene"):
        history.patch_scene(
            "u1", "sess", "s1", mod.ScenePatchTracked(name="Should Fail", expected_version=2)
        )
    history.patch_scene(
        "u1", "sess", "s1", mod.ScenePatchTracked(locked=False, expected_version=2)
    )
    history.patch_scene(
        "u1", "sess", "s1", mod.ScenePatchTracked(name="Renamed", expected_version=3)
    )


def test_programme_scene_delete_requires_explicit_confirmation_and_keeps_snapshot(tmp_path, monkeypatch):
    history, path = make_repo(tmp_path, monkeypatch)
    programme_before = read_session(path)["programme_snapshot_json"]
    with pytest.raises(StudioInvariantError, match="explicit confirmation"):
        history.delete_scene(
            "u1", "sess", "s1", mod.SceneDeleteTracked(expected_version=1)
        )
    history.delete_scene(
        "u1",
        "sess",
        "s1",
        mod.SceneDeleteTracked(expected_version=1, confirm_programme_reference=True),
    )
    session = read_session(path)
    assert session["preview_scene_id"] == "s2"
    assert session["programme_snapshot_json"] == programme_before
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT 1 FROM shared_sky_scenes WHERE id='s1'").fetchone() is None


def test_graphic_is_real_text_source_with_safe_typed_style(tmp_path, monkeypatch):
    history, path = make_repo(tmp_path, monkeypatch)
    source_id = history.create_graphic(
        "u1",
        "sess",
        mod.GraphicCreateRequest(
            kind="lower_third",
            name="Guest Lower Third",
            text="Mary Ortiz",
            secondary_text="Co-owner",
            style={"text_color": "#ffffff", "background_color": "#112233", "font_size": 48},
            expected_version=1,
        ),
    )
    source = read_source(path, source_id)
    assert source["source_type"] == "text"
    assert source["config"]["graphic"]["kind"] == "lower_third"
    assert source["config"]["graphic"]["secondary_text"] == "Co-owner"
    assert source["config"]["privacy"] == "programme_safe"
    assert history.state("u1", "sess")["undo_action"] == "source_create"


def test_locked_source_cannot_be_batch_transformed_or_deleted(tmp_path, monkeypatch):
    history, path = make_repo(tmp_path, monkeypatch)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE shared_sky_sources SET locked=1 WHERE id='src1'")
    with pytest.raises(StudioInvariantError, match="locked"):
        history.batch_transform(
            "u1",
            "sess",
            mod.BatchTransformRequest(
                expected_version=1,
                items=[mod.BatchTransformItem(source_id="src1", transform={"x": 0.1})],
            ),
        )
    with pytest.raises(StudioInvariantError, match="locked"):
        history.delete_sources(
            "u1", "sess", mod.BatchDeleteRequest(source_ids=["src1"], expected_version=1)
        )


def test_history_operations_reject_stale_operator_version(tmp_path, monkeypatch):
    history, _ = make_repo(tmp_path, monkeypatch)
    with pytest.raises(StudioConflict, match="version conflict"):
        history.create_scene(
            "u1",
            "sess",
            mod.SceneCreateTracked(name="Late", expected_version=99),
        )
