from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import aura_music_studio.video_scene_local_revision as local


def _request(authenticated: bool = True):
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    if authenticated:
        request.state.member = SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))
    return request


def _body(**overrides):
    values = {
        "edit_scope": "object",
        "target_description": "the red mug on the desk",
        "desired_change": "make the mug deep blue",
        "preserve_notes": "keep the actor, desk, camera and lighting unchanged",
    }
    values.update(overrides)
    return local.LocalizedSceneRevisionRequest(**values)


def test_prompt_is_explicitly_localized_and_preserving():
    prompt = local.localized_revision_prompt(_body())
    assert "Target only: the red mug on the desk" in prompt
    assert "make the mug deep blue" in prompt
    assert "Preserve all other scene content" in prompt


def test_broad_change_cannot_use_localized_contract():
    with pytest.raises(HTTPException) as exc:
        local.localized_revision_prompt(_body(preserve_everything_else=False))
    assert exc.value.status_code == 400


def test_authentication_happens_before_scene_lookup(monkeypatch):
    monkeypatch.setattr(local, "_scene", lambda *_: (_ for _ in ()).throw(AssertionError("scene lookup leaked")))
    with pytest.raises(HTTPException) as exc:
        local.render_localized_scene_revision("secret", "scene-1", _body(), _request(False))
    assert exc.value.status_code == 401


def test_existing_output_required(monkeypatch):
    monkeypatch.setattr(local, "_scene", lambda *_: ({"scenes": []}, {"id": "scene-1", "output_element_id": ""}))
    with pytest.raises(HTTPException) as exc:
        local.render_localized_scene_revision("demo", "scene-1", _body(), _request())
    assert exc.value.status_code == 409


def test_localized_revision_delegates_to_governed_scene_renderer_and_records_evidence(monkeypatch):
    timeline = {
        "scenes": [{"id": "scene-1", "output_element_id": "video-old", "render": {"directive_id": "d-new"}}]
    }
    captured = {}
    monkeypatch.setattr(local, "_scene", lambda *_: (timeline, timeline["scenes"][0]))
    monkeypatch.setattr(local, "_read", lambda *_: timeline)
    monkeypatch.setattr(local, "_write", lambda project_name, data: captured.setdefault("written", data))

    def fake_render(project_name, scene_id, body, request):
        captured["prompt"] = body.prompt_override
        return {"scene": {"id": scene_id}, "resource_governance": {"reservation_id": "r1"}}

    monkeypatch.setattr(local, "render_video_scene", fake_render)
    result = local.render_localized_scene_revision("demo", "scene-1", _body(), _request())
    evidence = result["localized_revision"]
    assert evidence["source_output_element_id"] == "video-old"
    assert evidence["localization_method"] == "instruction_scoped_scene_regeneration"
    assert evidence["pixel_mask_enforced"] is False
    assert evidence["unchanged_regions_guaranteed"] is False
    assert result["automatic_pixel_localization_guaranteed"] is False
    assert result["grants_esp_role_or_permission"] is False
    assert result["alters_billing_or_membership"] is False
    assert "Target only" in captured["prompt"]


def test_production_overlay_dispatches_localized_revision_to_auth_boundary():
    from aura_music_studio.creative_version_autopromotion import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        "/creative/projects/hidden/video-timeline/scenes/scene-1/localized-revision",
        json={
            "edit_scope": "background",
            "target_description": "the wall behind the presenter",
            "desired_change": "make it warm beige",
        },
    )
    assert response.status_code == 401
