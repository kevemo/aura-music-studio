from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import aura_music_studio.video_music_sync as sync
import aura_music_studio.video_scene_render as scene_render
import aura_music_studio.video_scene_timeline as timeline
from aura_music_studio.creative_version_autopromotion import router as production_creative_router


def _member_app(router=sync.router) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))
        return await call_next(request)

    app.include_router(router)
    return app


def _patch_project(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "_project_dir", lambda project_name: tmp_path)
    monkeypatch.setattr(timeline, "_project_dir", lambda project_name: tmp_path)


def _create_scene(tmp_path):
    data = timeline._empty_timeline("demo")
    data["scenes"] = [
        {
            "id": "scene-1",
            "label": "Chorus",
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "description": "Performance shot",
            "shot_type": "medium",
            "camera_direction": "slow orbit",
            "continuity_notes": "",
            "continuity_profile_ids": [],
            "preserve_element_ids": [],
            "reference_ids": [],
            "output_element_id": None,
            "status": "ready",
            "order": 0,
            "created_at": sync._now(),
            "updated_at": sync._now(),
        }
    ]
    timeline._write("demo", data)


def test_sync_cue_crud_is_bounded_and_preserves_security_flags(tmp_path, monkeypatch):
    _patch_project(tmp_path, monkeypatch)
    client = TestClient(_member_app())

    first = client.post(
        "/creative/projects/demo/video-sync/cues",
        json={
            "id": "chorus-start",
            "kind": "section",
            "at_seconds": 10.0,
            "end_seconds": 20.0,
            "label": "Chorus",
            "source_element_id": "song-master",
        },
    )
    assert first.status_code == 200
    assert first.json()["cue_count"] == 1
    assert first.json()["grants_esp_role_or_permission"] is False
    assert first.json()["alters_billing_or_membership"] is False

    second = client.post(
        "/creative/projects/demo/video-sync/cues",
        json={"id": "lyric-1", "kind": "lyric", "at_seconds": 12.5, "text": "Sparkles and glistens"},
    )
    assert second.status_code == 200
    assert [cue["id"] for cue in second.json()["cues"]] == ["chorus-start", "lyric-1"]

    duplicate = client.post(
        "/creative/projects/demo/video-sync/cues",
        json={"id": "lyric-1", "kind": "lyric", "at_seconds": 13.0},
    )
    assert duplicate.status_code == 409

    invalid_beat = client.post(
        "/creative/projects/demo/video-sync/cues",
        json={"id": "beat-1", "kind": "beat", "at_seconds": 13.0, "end_seconds": 14.0},
    )
    assert invalid_beat.status_code == 422


def test_scene_bindings_require_existing_overlapping_cues_and_are_non_destructive(tmp_path, monkeypatch):
    _patch_project(tmp_path, monkeypatch)
    _create_scene(tmp_path)
    client = TestClient(_member_app())

    assert client.post(
        "/creative/projects/demo/video-sync/cues",
        json={"id": "downbeat-1", "kind": "downbeat", "at_seconds": 10.0, "bar_index": 8},
    ).status_code == 200
    assert client.post(
        "/creative/projects/demo/video-sync/cues",
        json={"id": "lyric-1", "kind": "lyric", "at_seconds": 14.25, "text": "Goddess of Light"},
    ).status_code == 200
    assert client.post(
        "/creative/projects/demo/video-sync/cues",
        json={"id": "outside", "kind": "beat", "at_seconds": 30.0},
    ).status_code == 200

    missing = client.put(
        "/creative/projects/demo/video-sync/scenes/scene-1/bindings",
        json={"cue_ids": ["missing"]},
    )
    assert missing.status_code == 400

    outside = client.put(
        "/creative/projects/demo/video-sync/scenes/scene-1/bindings",
        json={"cue_ids": ["outside"]},
    )
    assert outside.status_code == 409

    bound = client.put(
        "/creative/projects/demo/video-sync/scenes/scene-1/bindings",
        json={"cue_ids": ["downbeat-1", "lyric-1"]},
    )
    assert bound.status_code == 200
    body = bound.json()
    assert body["cue_ids"] == ["downbeat-1", "lyric-1"]
    assert body["sync_window"] == {"start_seconds": 10.0, "end_seconds": 14.25}
    assert body["scene_timing_unchanged"] is True

    saved_scene = timeline._read("demo")["scenes"][0]
    assert saved_scene["start_seconds"] == 10.0
    assert saved_scene["end_seconds"] == 20.0
    assert saved_scene["sync_cue_ids"] == ["downbeat-1", "lyric-1"]

    blocked_delete = client.delete("/creative/projects/demo/video-sync/cues/lyric-1")
    assert blocked_delete.status_code == 409


def test_sync_prompt_keeps_lyric_and_beat_timing_in_scene_render_direction():
    cues = [
        {"id": "beat-1", "kind": "beat", "at_seconds": 1.0, "end_seconds": None, "label": "", "text": ""},
        {"id": "lyric-1", "kind": "lyric", "at_seconds": 1.5, "end_seconds": None, "label": "", "text": "shine"},
    ]
    prompt = scene_render._scene_prompt(
        {"description": "Close performance", "shot_type": "", "camera_direction": "", "continuity_notes": ""},
        None,
        [],
        cues,
    )
    assert "Music synchronization anchors" in prompt
    assert "1.000s [beat]" in prompt
    assert "1.500s [lyric]: shine" in prompt


def test_sync_requires_member_and_is_mounted_in_production_creative_router(tmp_path, monkeypatch):
    _patch_project(tmp_path, monkeypatch)

    unauthenticated = FastAPI()
    unauthenticated.include_router(sync.router)
    assert TestClient(unauthenticated).get("/creative/projects/demo/video-sync").status_code == 401

    client = TestClient(_member_app(production_creative_router))
    response = client.get("/creative/projects/demo/video-sync")
    assert response.status_code == 200
    assert response.json()["project_name"] == "demo"
