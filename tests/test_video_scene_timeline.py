from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import aura_music_studio.video_scene_timeline as timeline
from aura_music_studio.creative_version_autopromotion import router as production_creative_router


def _member_app(router=timeline.router) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))
        return await call_next(request)

    app.include_router(router)
    return app


def test_scene_timeline_crud_preserves_security_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "_project_dir", lambda project_name: tmp_path)
    client = TestClient(_member_app())

    created = client.post(
        "/creative/projects/demo/video-timeline/scenes",
        json={
            "id": "scene-1",
            "label": "Opening",
            "start_seconds": 0,
            "end_seconds": 4.5,
            "description": "Wide establishing shot",
            "preserve_element_ids": ["lead-character"],
            "reference_ids": ["style-ref"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["scene_count"] == 1
    assert body["scenes"][0]["preserve_element_ids"] == ["lead-character"]
    assert body["grants_esp_role_or_permission"] is False
    assert body["alters_billing_or_membership"] is False

    updated = client.patch(
        "/creative/projects/demo/video-timeline/scenes/scene-1",
        json={"camera_direction": "Slow push in", "status": "ready"},
    )
    assert updated.status_code == 200
    assert updated.json()["scenes"][0]["camera_direction"] == "Slow push in"
    assert updated.json()["scenes"][0]["status"] == "ready"

    loaded = client.get("/creative/projects/demo/video-timeline")
    assert loaded.status_code == 200
    assert loaded.json()["scenes"][0]["id"] == "scene-1"


def test_scene_timeline_rejects_overlaps_and_duplicate_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "_project_dir", lambda project_name: tmp_path)
    client = TestClient(_member_app())
    first = {"id": "scene-1", "label": "One", "start_seconds": 0, "end_seconds": 5}
    assert client.post("/creative/projects/demo/video-timeline/scenes", json=first).status_code == 200
    assert client.post("/creative/projects/demo/video-timeline/scenes", json=first).status_code == 409

    overlap = {"id": "scene-2", "label": "Two", "start_seconds": 4, "end_seconds": 8}
    response = client.post("/creative/projects/demo/video-timeline/scenes", json=overlap)
    assert response.status_code == 409
    assert response.json()["detail"] == "Video scenes cannot overlap"


def test_scene_reorder_requires_exact_scene_set(tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "_project_dir", lambda project_name: tmp_path)
    client = TestClient(_member_app())
    for payload in (
        {"id": "scene-a", "label": "A", "start_seconds": 0, "end_seconds": 2},
        {"id": "scene-b", "label": "B", "start_seconds": 2, "end_seconds": 4},
    ):
        assert client.post("/creative/projects/demo/video-timeline/scenes", json=payload).status_code == 200

    bad = client.post("/creative/projects/demo/video-timeline/reorder", json={"scene_ids": ["scene-a"]})
    assert bad.status_code == 400

    good = client.post(
        "/creative/projects/demo/video-timeline/reorder",
        json={"scene_ids": ["scene-b", "scene-a"]},
    )
    assert good.status_code == 200
    assert [scene["id"] for scene in good.json()["scenes"]] == ["scene-b", "scene-a"]
    assert [scene["order"] for scene in good.json()["scenes"]] == [0, 1]


def test_scene_timeline_requires_member_and_is_present_in_production_creative_router(tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "_project_dir", lambda project_name: tmp_path)

    unauthenticated = FastAPI()
    unauthenticated.include_router(timeline.router)
    assert TestClient(unauthenticated).get("/creative/projects/demo/video-timeline").status_code == 401

    client = TestClient(_member_app(production_creative_router))
    response = client.get("/creative/projects/demo/video-timeline")
    assert response.status_code == 200
    assert response.json()["project_name"] == "demo"
