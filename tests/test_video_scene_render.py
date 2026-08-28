from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import aura_music_studio.video_scene_render as scene_render
from aura_music_studio.creative_version_autopromotion import router as production_creative_router


class _FakeStore:
    def __init__(self, project):
        self.project = project
        self.added = []

    def load(self):
        return SimpleNamespace(elements=[])

    def add_directive(self, directive):
        self.added.append(directive)
        return SimpleNamespace()


class _ResourceStore:
    def __init__(self):
        self.cancelled = []

    def reserve(self, **kwargs):
        assert kwargs["media_kind"] == "video"
        return {
            "reservation_id": "reservation-1",
            "units": 1,
            "billing_charge": False,
            "grants_esp_role_or_permission": False,
        }

    def cancel(self, reservation_id, *, user_id):
        self.cancelled.append((reservation_id, user_id))
        return True


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.member = SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))
    return request


def test_scene_render_builds_scoped_directive_and_uses_compute_governance(monkeypatch, tmp_path):
    data = {
        "schema_version": 1,
        "project_name": "demo",
        "updated_at": "now",
        "scenes": [{
            "id": "scene-1",
            "label": "Opening",
            "start_seconds": 0,
            "end_seconds": 5,
            "description": "A wide night skyline",
            "shot_type": "wide",
            "camera_direction": "slow push in",
            "continuity_notes": "keep lead wardrobe unchanged",
            "preserve_element_ids": ["lead"],
            "reference_ids": ["ref-style"],
            "output_element_id": None,
            "status": "ready",
        }],
    }
    resource = _ResourceStore()
    captured = {}
    monkeypatch.setattr(scene_render, "_read", lambda project_name: data)
    monkeypatch.setattr(scene_render, "_project_dir", lambda project_name: tmp_path)
    monkeypatch.setattr(scene_render, "CreativeProjectStore", _FakeStore)
    monkeypatch.setattr(scene_render, "creative_render_resource_store", resource)
    monkeypatch.setattr(scene_render, "_write", lambda project_name, payload: captured.setdefault("timeline", payload))
    monkeypatch.setattr(
        scene_render,
        "queue_creative_render",
        lambda project_name, directive_id, body, request: {
            "directive": {"id": directive_id, "target_kind": "video"},
            "submission": {
                "prompt_id": "prompt-1",
                "provider": "comfyui",
                "workflow_name": "video.json",
            },
        },
    )

    response = scene_render.render_video_scene(
        "demo",
        "scene-1",
        scene_render.SceneRenderRequest(width=1024, height=1024, frames=121),
        _request(),
    )
    assert response["scene"]["status"] == "rendering"
    assert response["scene"]["render"]["prompt_id"] == "prompt-1"
    assert response["resource_governance"]["billing_charge"] is False
    assert response["grants_esp_role_or_permission"] is False
    assert response["alters_billing_or_membership"] is False
    assert captured["timeline"]["scenes"][0]["render"]["directive_id"] == response["directive"]["id"]


def test_failed_provider_submission_releases_compute_reservation(monkeypatch, tmp_path):
    data = {
        "schema_version": 1,
        "project_name": "demo",
        "updated_at": "now",
        "scenes": [{
            "id": "scene-1", "label": "Opening", "start_seconds": 0, "end_seconds": 5,
            "description": "Opening shot", "preserve_element_ids": [], "reference_ids": [],
            "output_element_id": None, "status": "ready",
        }],
    }
    resource = _ResourceStore()
    monkeypatch.setattr(scene_render, "_read", lambda project_name: data)
    monkeypatch.setattr(scene_render, "_project_dir", lambda project_name: tmp_path)
    monkeypatch.setattr(scene_render, "CreativeProjectStore", _FakeStore)
    monkeypatch.setattr(scene_render, "creative_render_resource_store", resource)

    def fail(*args, **kwargs):
        raise HTTPException(502, "renderer failed")

    monkeypatch.setattr(scene_render, "queue_creative_render", fail)
    with pytest.raises(HTTPException) as exc:
        scene_render.render_video_scene(
            "demo", "scene-1", scene_render.SceneRenderRequest(), _request()
        )
    assert exc.value.status_code == 502
    assert resource.cancelled == [("reservation-1", "member-1")]


def test_scene_sync_links_exactly_one_video_output_and_retains_previous(monkeypatch):
    data = {
        "schema_version": 1,
        "project_name": "demo",
        "updated_at": "now",
        "scenes": [{
            "id": "scene-1", "status": "rendering", "output_element_id": "old-video",
            "render": {"directive_id": "dir-1"},
        }],
    }
    captured = {}
    monkeypatch.setattr(scene_render, "_read", lambda project_name: data)
    monkeypatch.setattr(scene_render, "_write", lambda project_name, payload: captured.setdefault("timeline", payload))
    monkeypatch.setattr(
        scene_render,
        "sync_creative_outputs",
        lambda project_name, directive_id, request: {
            "imported_elements": [{"id": "new-video", "kind": "video", "source_ref": "output/new.mp4"}]
        },
    )
    response = scene_render.sync_video_scene_output("demo", "scene-1", _request())
    assert response["scene"]["output_element_id"] == "new-video"
    assert response["scene"]["status"] == "rendered"
    assert response["previous_output_retained"] is True
    assert response["alters_billing_or_membership"] is False
    assert captured["timeline"]["scenes"][0]["render"]["synced"] is True


def test_production_overlay_dispatches_scene_render_and_enforces_member_boundary():
    app = FastAPI()
    app.include_router(production_creative_router)
    client = TestClient(app)
    response = client.post(
        "/creative/projects/demo/video-timeline/scenes/scene-1/render",
        json={},
    )
    assert response.status_code == 401
    assert response.status_code != 404
