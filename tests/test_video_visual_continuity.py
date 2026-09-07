from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import aura_music_studio.video_scene_render as scene_render
import aura_music_studio.video_visual_continuity as continuity
from aura_music_studio.creative_version_autopromotion import router as production_creative_router


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.member = SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))
    return request


def test_resolve_profiles_is_ordered_and_rejects_missing(monkeypatch):
    monkeypatch.setattr(
        continuity,
        "_read",
        lambda project_name: {
            "schema_version": 1,
            "project_name": project_name,
            "updated_at": "now",
            "profiles": [
                {"id": "style-1", "label": "Neon", "kind": "style"},
                {"id": "lead-1", "label": "Lead", "kind": "character"},
            ],
        },
    )
    resolved = continuity.resolve_profiles("demo", ["lead-1", "style-1"])
    assert [item["id"] for item in resolved] == ["lead-1", "style-1"]
    with pytest.raises(HTTPException) as exc:
        continuity.resolve_profiles("demo", ["missing"])
    assert exc.value.status_code == 409


def test_continuity_prompt_contains_positive_and_negative_locks():
    prompt = continuity.continuity_prompt([
        {
            "id": "lead-1",
            "label": "Lead singer",
            "kind": "character",
            "description": "same adult performer",
            "appearance_lock": "short dark hair",
            "wardrobe_lock": "black jacket",
            "palette_lock": "warm amber key light",
            "negative_constraints": ["do not change face", "do not change jacket"],
        }
    ])
    assert "Continuity lock" in prompt
    assert "short dark hair" in prompt
    assert "black jacket" in prompt
    assert "do not change face" in prompt


def test_scene_render_applies_profile_references_and_preserve_locks(monkeypatch, tmp_path):
    data = {
        "schema_version": 1,
        "project_name": "demo",
        "updated_at": "now",
        "scenes": [{
            "id": "scene-1",
            "label": "Opening",
            "start_seconds": 0,
            "end_seconds": 5,
            "description": "Singer walks through a neon street",
            "shot_type": "medium",
            "camera_direction": "tracking",
            "continuity_notes": "same performer",
            "continuity_profile_ids": ["lead-1", "style-1"],
            "reference_ids": ["scene-ref"],
            "preserve_element_ids": ["scene-anchor"],
            "output_element_id": None,
            "status": "ready",
        }],
    }

    class Store:
        added = None
        def __init__(self, project):
            pass
        def load(self):
            return SimpleNamespace(elements=[])
        def add_directive(self, directive):
            Store.added = directive

    class Resource:
        def reserve(self, **kwargs):
            return {"reservation_id": "r1", "billing_charge": False}
        def cancel(self, reservation_id, *, user_id):
            return True

    monkeypatch.setattr(scene_render, "_read", lambda project_name: data)
    monkeypatch.setattr(scene_render, "_write", lambda project_name, payload: payload)
    monkeypatch.setattr(scene_render, "_project_dir", lambda project_name: tmp_path)
    monkeypatch.setattr(scene_render, "CreativeProjectStore", Store)
    monkeypatch.setattr(scene_render, "creative_render_resource_store", Resource())
    monkeypatch.setattr(
        scene_render,
        "resolve_profiles",
        lambda project_name, ids: [
            {
                "id": "lead-1", "label": "Lead", "kind": "character",
                "appearance_lock": "same face and hair",
                "reference_ids": ["lead-ref"],
                "preserve_element_ids": ["lead-anchor"],
            },
            {
                "id": "style-1", "label": "Night style", "kind": "style",
                "palette_lock": "blue and magenta neon",
                "reference_ids": ["style-ref", "scene-ref"],
                "preserve_element_ids": [],
            },
        ],
    )
    monkeypatch.setattr(
        scene_render,
        "queue_creative_render",
        lambda project_name, directive_id, body, request: {
            "directive": {"id": directive_id, "target_kind": "video"},
            "submission": {"prompt_id": "p1", "provider": "comfyui", "workflow_name": "video.json"},
        },
    )

    result = scene_render.render_video_scene(
        "demo", "scene-1", scene_render.SceneRenderRequest(width=1024, height=1024, frames=121), _request()
    )
    directive = Store.added
    assert directive is not None
    assert directive.reference_ids == ["scene-ref", "lead-ref", "style-ref"]
    assert directive.preserve_element_ids == ["scene-anchor", "lead-anchor"]
    assert "same face and hair" in directive.instruction
    assert "blue and magenta neon" in directive.instruction
    assert directive.metadata["video_scene"]["continuity_profile_ids"] == ["lead-1", "style-1"]
    assert result["continuity_profiles_applied"] == ["lead-1", "style-1"]
    assert result["grants_esp_role_or_permission"] is False
    assert result["alters_billing_or_membership"] is False


def test_scene_prompt_override_cannot_drop_bound_continuity():
    prompt = scene_render._scene_prompt(
        {"description": "ignored"},
        "Make the camera lower",
        [{"id": "lead", "label": "Lead", "kind": "character", "appearance_lock": "same face"}],
    )
    assert "Make the camera lower" in prompt
    assert "same face" in prompt


def test_scene_models_reject_duplicate_continuity_bindings():
    from aura_music_studio.video_scene_timeline import SceneCreateRequest

    with pytest.raises(ValueError):
        SceneCreateRequest(
            id="scene-1",
            label="Scene",
            start_seconds=0,
            end_seconds=5,
            continuity_profile_ids=["lead", "lead"],
        )


def test_production_overlay_mounts_continuity_routes_and_requires_member():
    app = FastAPI()
    app.include_router(production_creative_router)
    client = TestClient(app)
    response = client.get("/creative/projects/demo/video-continuity")
    assert response.status_code == 401
