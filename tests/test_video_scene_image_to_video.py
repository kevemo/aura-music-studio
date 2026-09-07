from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aura_music_studio import video_scene_image_to_video as subject
from aura_music_studio import video_scene_timeline as timeline
from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore
from aura_music_studio.plans import get_plan


def _request(plan_id: str = "pro"):
    member = SimpleNamespace(user_id="member-1", plan=get_plan(plan_id))
    return SimpleNamespace(state=SimpleNamespace(member=member))


def test_scene_request_does_not_accept_source_paths_workflows_or_renderer_variables():
    properties = subject.SceneImageToVideoRenderRequest.model_json_schema()["properties"]
    assert "rights_confirmed" in properties
    assert "source_element_id" not in properties
    assert "source_path" not in properties
    assert "workflow_name" not in properties
    assert "model" not in properties
    assert "provider_url" not in properties
    assert "variables" not in properties


def test_scene_image_to_video_requires_rights_before_timeline_lookup(monkeypatch):
    monkeypatch.setattr(
        subject,
        "_read",
        lambda _project_name: (_ for _ in ()).throw(AssertionError("timeline lookup must not run")),
    )
    body = subject.SceneImageToVideoRenderRequest(rights_confirmed=False)
    with pytest.raises(HTTPException) as exc:
        subject.render_scene_image_to_video("project-a", "scene-a", body, _request())
    assert exc.value.status_code == 400


def test_scene_frame_count_defaults_to_scene_duration():
    scene = {"start_seconds": 10.0, "end_seconds": 12.0}
    assert subject._scene_frames(scene, None, 24.0) == 49
    assert subject._scene_frames(scene, 80, 24.0) == 80


def test_scene_image_to_video_delegates_to_hardened_renderer_and_links_scene(monkeypatch):
    data = {
        "schema_version": 1,
        "project_name": "project-a",
        "updated_at": "now",
        "scenes": [
            {
                "id": "scene-a",
                "label": "Opening",
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "description": "Slow push toward the subject",
                "shot_type": "medium",
                "camera_direction": "push in",
                "continuity_notes": "preserve face and clothing",
                "continuity_profile_ids": [],
                "source_image_element_id": "el-image",
                "status": "ready",
            }
        ],
    }
    monkeypatch.setattr(subject, "_read", lambda _project_name: data)
    written = {}
    monkeypatch.setattr(subject, "_write", lambda project_name, payload: written.update(project=project_name, data=payload) or payload)
    monkeypatch.setattr(subject, "resolve_profiles", lambda _project_name, _profile_ids: [])

    seen = {}

    def fake_render(project_name, body, request):
        seen["project_name"] = project_name
        seen["body"] = body
        seen["request"] = request
        return {
            "directive": {"id": "dir-i2v"},
            "submission": {
                "prompt_id": "prompt-i2v",
                "provider": "comfyui",
                "workflow_name": "operator-i2v.json",
            },
            "resource_governance": {"reservation_id": "reserve-1"},
            "commercial_entitlements": {"video_generation": {"ok": True}},
        }

    monkeypatch.setattr(subject, "render_project_image_to_video", fake_render)
    body = subject.SceneImageToVideoRenderRequest(rights_confirmed=True, fps=24.0)
    request = _request("pro")
    response = subject.render_scene_image_to_video("project-a", "scene-a", body, request)

    assert seen["project_name"] == "project-a"
    assert seen["request"] is request
    assert seen["body"].source_element_id == "el-image"
    assert seen["body"].rights_confirmed is True
    assert seen["body"].frames == 49
    assert "Slow push toward the subject" in seen["body"].instruction
    assert response["scene"]["status"] == "rendering"
    assert response["scene"]["render"]["mode"] == "image_to_video"
    assert response["scene"]["render"]["directive_id"] == "dir-i2v"
    assert response["scene"]["render"]["source_image_element_id"] == "el-image"
    assert response["source"]["raw_filesystem_path_exposed"] is False
    assert response["grants_esp_role_or_permission"] is False
    assert response["alters_billing_or_membership"] is False
    assert written["project"] == "project-a"


def test_scene_source_binding_must_reference_project_image(tmp_path, monkeypatch):
    store = CreativeProjectStore(tmp_path)
    store.initialize(project_name="project-a", title="Project A")
    audio = CreativeElement(kind="audio", label="Audio", status="ready", source_ref="audio.wav")
    store.add_element(audio)
    image = CreativeElement(kind="image", label="Image", status="ready", source_ref="image.png")
    store.add_element(image)
    monkeypatch.setattr(timeline, "_project_dir", lambda _project_name: tmp_path)

    with pytest.raises(HTTPException) as exc:
        timeline._validate_source_image_element("project-a", audio.id)
    assert exc.value.status_code == 400
    timeline._validate_source_image_element("project-a", image.id)


def test_scene_image_to_video_route_is_mounted_in_production_aggregate():
    from aura_music_studio.creative_version_autopromotion import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/creative/projects/project-a/video-timeline/scenes/scene-a/render-image-to-video",
        json={"rights_confirmed": False},
    )

    # The mounted production aggregate authenticates before route-local rights validation.
    # An unmounted route would return 404 instead of the expected fail-closed 401.
    assert response.status_code == 401
    assert "Authenticated member" in response.json()["detail"]
