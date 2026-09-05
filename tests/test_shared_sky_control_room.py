from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aura_music_studio import shared_sky_control_room as mod


class FakeGraph:
    def __init__(self, project_id="p1", user_id="u1"):
        self.project_id = project_id
        self.user_id = user_id
        self.scenes = {
            "s1": {"id": "s1", "project_id": project_id, "user_id": user_id, "name": "Opening", "position": 0, "layout_key": "solo", "transition_key": "fade", "transition_ms": 350, "sources": []},
            "s2": {"id": "s2", "project_id": project_id, "user_id": user_id, "name": "Interview", "position": 1, "layout_key": "interview", "transition_key": "fade", "transition_ms": 350, "sources": []},
        }
        self.sources = {}
        self.events_seen = []
        self.broadcasts = {}
        self.created = 0

    def project(self, user_id, project_id):
        if user_id != self.user_id or project_id != self.project_id:
            raise KeyError(project_id)
        scenes = sorted((self.scene(user_id, scene_id) for scene_id in self.scenes), key=lambda row: row["position"])
        return {"id": project_id, "user_id": user_id, "name": "Show", "scenes": scenes}

    def scene(self, user_id, scene_id):
        if user_id != self.user_id or scene_id not in self.scenes:
            raise KeyError(scene_id)
        row = dict(self.scenes[scene_id])
        row["sources"] = [dict(self.sources[source_id]) for source_id in self.sources if self.sources[source_id]["scene_id"] == scene_id]
        return row

    def create_scene(self, user_id, project_id, body):
        self.created += 1
        scene_id = f"copy{self.created}"
        self.scenes[scene_id] = {
            "id": scene_id, "project_id": project_id, "user_id": user_id, "name": body.name,
            "position": len(self.scenes), "layout_key": body.layout_key, "transition_key": body.transition_key,
            "transition_ms": body.transition_ms, "sources": [],
        }
        return self.scene(user_id, scene_id)

    def update_scene(self, user_id, scene_id, body):
        row = self.scenes[scene_id]
        for key in ("name", "position", "layout_key", "transition_key", "transition_ms"):
            if hasattr(body, key) and getattr(body, key) is not None:
                row[key] = getattr(body, key)
        return self.scene(user_id, scene_id)

    def create_source(self, user_id, scene_id, body):
        source_id = f"src{len(self.sources)+1}"
        self.sources[source_id] = {
            "id": source_id, "scene_id": scene_id, "project_id": self.project_id, "user_id": user_id,
            "source_type": body.source_type, "name": body.name, "config": dict(body.config),
            "visible": body.visible, "locked": body.locked, "z_index": body.z_index,
        }
        return dict(self.sources[source_id])

    def source(self, user_id, source_id):
        if source_id not in self.sources:
            raise KeyError(source_id)
        return dict(self.sources[source_id])

    def update_source(self, user_id, source_id, body):
        row = self.sources[source_id]
        for key in ("name", "config", "visible", "locked", "z_index"):
            if hasattr(body, key) and getattr(body, key) is not None:
                row[key] = getattr(body, key)
        return dict(row)

    def broadcast(self, user_id, broadcast_id):
        if broadcast_id not in self.broadcasts:
            raise KeyError(broadcast_id)
        return dict(self.broadcasts[broadcast_id])

    def preflight(self, user_id, broadcast_id):
        return {"ok": True, "broadcast_id": broadcast_id}

    def event(self, user_id, broadcast_id, event_type, payload=None):
        self.events_seen.append((broadcast_id, event_type, payload or {}))


def make_repo(tmp_path: Path, graph: FakeGraph) -> mod.StudioRepository:
    path = tmp_path / "studio.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE shared_sky_projects(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL);
            CREATE TABLE shared_sky_scenes(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,position INTEGER NOT NULL);
            """
        )
        con.execute("INSERT INTO shared_sky_projects VALUES(?,?,?)", (graph.project_id, graph.user_id, "Show"))
        for scene in graph.scenes.values():
            con.execute("INSERT INTO shared_sky_scenes VALUES(?,?,?,?,?)", (scene["id"], graph.project_id, graph.user_id, scene["name"], scene["position"]))
    return mod.StudioRepository(str(path))


def add_source(graph: FakeGraph, scene="s1", source_type="image", *, privacy="programme_safe", url=None, audio=None):
    source_id = f"src{len(graph.sources)+1}"
    config = {"privacy": privacy, "transform": {"x": 0, "y": 0, "width": 1, "height": 1}}
    if url is not None:
        config["url"] = url
    if audio is not None:
        config["audio"] = audio
    graph.sources[source_id] = {
        "id": source_id, "scene_id": scene, "project_id": graph.project_id, "user_id": graph.user_id,
        "source_type": source_type, "name": source_id, "config": config, "visible": True, "locked": False,
        "z_index": len(graph.sources),
    }
    return source_id


def test_transform_normalisation_and_crop_guard():
    result = mod.normalize_transform({"x": 9, "opacity": 7, "rotation": 540, "crop_left": .1})
    assert result["x"] == 4.0
    assert result["opacity"] == 1.0
    assert -180 <= result["rotation"] < 180
    with pytest.raises(mod.StudioInvariantError):
        mod.normalize_transform({"crop_left": .7, "crop_right": .4})


def test_browser_source_url_security():
    assert mod.validate_web_source_url("https://example.com/widget") == "https://example.com/widget"
    for url in ("file:///etc/passwd", "http://127.0.0.1/x", "http://localhost/x", "https://user:pass@example.com"):
        with pytest.raises(mod.StudioInvariantError):
            mod.validate_web_source_url(url)


def test_no_provider_secrets_in_presets_or_sources():
    mod.validate_no_secrets({"asset_id": "abc", "style": {"opacity": 1}})
    with pytest.raises(mod.StudioInvariantError):
        mod.validate_no_secrets({"oauth_token": "should-never-be-here"})


def test_audio_meter_uses_real_samples_not_animation():
    unavailable = mod.calculate_audio_meter([])
    assert unavailable["available"] is False and unavailable["rms"] is None
    meter = mod.calculate_audio_meter([0.0, 0.5, -0.5, 1.0])
    assert meter["available"] is True
    assert meter["peak"] == 1.0
    assert meter["clipping"] is True
    assert meter["dbfs"] < 0


@pytest.mark.parametrize("profile", ["landscape-1080", "portrait-1080"])
@pytest.mark.parametrize("count", list(range(1, 9)))
def test_participant_layouts_are_deterministic_and_bounded(profile, count):
    first = mod.participant_layout("grid", count, profile)
    second = mod.participant_layout("grid", count, profile)
    assert first == second and len(first) == count
    assert all(0 <= tile["x"] <= 1 and 0 <= tile["y"] <= 1 and tile["width"] > 0 and tile["height"] > 0 for tile in first)


def test_preview_and_programme_are_isolated_and_persisted(tmp_path):
    graph = FakeGraph()
    source_id = add_source(graph, "s1")
    repo = make_repo(tmp_path, graph)
    service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1"))["session"]
    assert session["preview_scene_id"] == "s1" and session["programme_scene_id"] is None
    cut = service.cut("u1", session["id"], session["version"])["session"]
    assert cut["programme_scene_id"] == "s1"
    assert cut["programme_snapshot"]["sources"][0]["id"] == source_id
    graph.sources[source_id]["config"]["transform"]["x"] = 0.44
    refreshed = repo.get_session("u1", cut["id"])
    assert refreshed["programme_snapshot"]["sources"][0]["config"]["transform"]["x"] == 0.0
    reopened = mod.StudioRepository(repo.db_path).get_session("u1", cut["id"])
    assert reopened["programme_scene_id"] == "s1"
    assert len(repo.versions("u1", cut["id"])) >= 2


def test_preview_selection_never_changes_programme(tmp_path):
    graph = FakeGraph(); add_source(graph, "s1")
    repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1"))["session"]
    session = service.cut("u1", session["id"], session["version"])["session"]
    changed = service.select_preview("u1", session["id"], mod.PreviewSelect(scene_id="s2", expected_version=session["version"]))["session"]
    assert changed["preview_scene_id"] == "s2"
    assert changed["programme_scene_id"] == "s1"


def test_transition_locks_double_actions_and_reduced_motion(tmp_path):
    graph = FakeGraph(); add_source(graph)
    repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1"))["session"]
    started = service.begin_transition("u1", session["id"], mod.TransitionRequest(expected_version=session["version"], transition_key="fade", duration_ms=900, reduced_motion=True))["session"]
    assert started["transition_state"] == "in_progress" and started["transition"]["duration_ms"] == 0
    with pytest.raises(mod.StudioConflict):
        service.begin_transition("u1", started["id"], mod.TransitionRequest(expected_version=started["version"], transition_key="fade"))
    done = service.complete_transition("u1", started["id"], mod.TransitionComplete(expected_version=started["version"], transition_id=started["transition"]["transition_id"]))["session"]
    assert done["transition_state"] == "idle" and done["programme_scene_id"] == "s1"


def test_stale_tab_conflict_does_not_overwrite_newer_version(tmp_path):
    graph = FakeGraph(); repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1"))["session"]
    fresh = service.select_preview("u1", session["id"], mod.PreviewSelect(scene_id="s2", expected_version=session["version"]))["session"]
    with pytest.raises(mod.StudioConflict):
        service.select_preview("u1", session["id"], mod.PreviewSelect(scene_id="s1", expected_version=session["version"]))
    assert repo.get_session("u1", session["id"])["version"] == fresh["version"]


def test_private_backstage_source_cannot_leak_to_programme(tmp_path):
    graph = FakeGraph(); add_source(graph, privacy="private")
    repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1"))["session"]
    with pytest.raises(mod.StudioInvariantError, match="private/backstage"):
        service.cut("u1", session["id"], session["version"])
    assert repo.get_session("u1", session["id"])["programme_scene_id"] is None


def test_live_programme_commit_fails_closed_without_chat2_contract(tmp_path):
    graph = FakeGraph(); add_source(graph)
    graph.broadcasts["b1"] = {"id": "b1", "project_id": "p1", "state": "live"}
    repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1", broadcast_id="b1"))["session"]
    with pytest.raises(mod.StudioTransportError, match="Chat 2 live programme commit contract"):
        service.cut("u1", session["id"], session["version"])
    assert repo.get_session("u1", session["id"])["programme_scene_id"] is None


def test_recording_state_is_explicitly_unsupported_until_chat2_contract():
    graph = FakeGraph(); graph.broadcasts["b1"] = {"id": "b1", "project_id": "p1", "state": "draft"}
    result = mod.SharedSkyTransportCompatibilityAdapter(graph).recording_capabilities("u1", "b1")
    assert result == {"supported": False, "state": "unavailable", "reason": "Chat 2 recording contract not merged"}


def test_brand_kit_refs_are_versioned_and_secret_safe(tmp_path):
    graph = FakeGraph(); repo = make_repo(tmp_path, graph)
    kit = repo.upsert_brand_kit("u1", "p1", mod.BrandKitUpsert(name="Main", colors=["#123456"], asset_refs={"logo": "asset-123"}))
    assert kit["version"] == 1 and kit["config"]["asset_refs"]["logo"] == "asset-123"
    updated = repo.upsert_brand_kit("u1", "p1", mod.BrandKitUpsert(name="Main 2", colors=["#abcdef"], expected_version=1), kit["id"])
    assert updated["version"] == 2
    with pytest.raises(mod.StudioConflict):
        repo.upsert_brand_kit("u1", "p1", mod.BrandKitUpsert(name="stale", expected_version=1), kit["id"])
    with pytest.raises(mod.StudioInvariantError):
        repo.upsert_brand_kit("u1", "p1", mod.BrandKitUpsert(name="bad", style={"stream_key": "x"}))


def test_duplicate_scene_deep_copies_sources_and_reorder_is_exact():
    graph = FakeGraph(); source_id = add_source(graph, "s1")
    service = mod.StudioService.__new__(mod.StudioService); service.graph = graph
    copied = mod.StudioService.duplicate_scene(service, "u1", "s1")
    assert copied["name"].endswith("Copy") and copied["sources"][0]["id"] != source_id
    ids = [scene["id"] for scene in graph.project("u1", "p1")["scenes"]]
    reordered = mod.StudioService.reorder_scenes(service, "u1", "p1", list(reversed(ids)))
    assert [row["id"] for row in reordered] == list(reversed(ids))
    with pytest.raises(mod.StudioInvariantError):
        mod.StudioService.reorder_scenes(service, "u1", "p1", ["s1"])


def test_transform_and_audio_autosave_with_session_version(tmp_path):
    graph = FakeGraph(); image = add_source(graph, "s1"); mic = add_source(graph, "s1", source_type="microphone", audio={})
    repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    session = service.create_session("u1", mod.StudioSessionCreate(project_id="p1"))["session"]
    transformed = service.update_transform("u1", session["id"], image, mod.SourceTransformPatch(expected_session_version=session["version"], transform={"x": .25, "width": .5}))
    assert transformed["source"]["config"]["transform"]["x"] == .25
    version = transformed["session"]["version"]
    mixed = service.update_audio("u1", session["id"], mic, mod.AudioPatch(expected_session_version=version, audio={"gain": 9, "pan": -2, "muted": True}))
    assert mixed["source"]["config"]["audio"]["gain"] == 4.0
    assert mixed["source"]["config"]["audio"]["pan"] == -1.0
    assert mixed["source"]["config"]["audio"]["muted"] is True


def test_widget_bindings_are_display_only_for_external_domains(tmp_path):
    graph = FakeGraph(); repo = make_repo(tmp_path, graph); service = mod.StudioService(repo, graph)
    gift = service.widget_binding("gift_goal", {"goal_id": "g1"})
    assert gift["authoritative_state_owned_externally"] is True
    with pytest.raises(mod.StudioInvariantError):
        service.widget_binding("gift_goal", {"access_token": "nope"})
    with pytest.raises(mod.StudioInvariantError):
        service.widget_binding("unknown", {})


def test_studio_ui_hotkeys_are_focus_safe_and_meters_start_unavailable():
    assert "input,textarea,select,[contenteditable=true]" in mod.STUDIO_JS
    assert "meter unavailable" in mod.STUDIO_JS
    assert "requestAnimationFrame(tick)" in mod.STUDIO_JS
    assert "OFF AIR / no programme snapshot" in mod.STUDIO_JS
