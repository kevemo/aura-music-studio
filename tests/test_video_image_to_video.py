import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore
from aura_music_studio.creative_renderers import RendererInput, RendererSubmission
from aura_music_studio.plans import get_plan
from aura_music_studio import video_image_to_video as subject


def _member(plan_id: str = "pro"):
    return SimpleNamespace(user_id="member-1", plan=get_plan(plan_id))


def _request(plan_id: str = "pro"):
    return SimpleNamespace(state=SimpleNamespace(member=_member(plan_id)))


def _ready_image_project(tmp_path):
    store = CreativeProjectStore(tmp_path)
    store.initialize(project_name="project-a", title="Project A")
    source = tmp_path / "source.png"
    source.write_bytes(b"not-real-image-but-local-test-data")
    element = CreativeElement(
        kind="image",
        label="Source image",
        status="ready",
        source_type="uploaded",
        source_ref="source.png",
    )
    store.add_element(element)
    return store, element, source


def test_public_request_does_not_accept_paths_workflows_models_or_raw_variables():
    properties = subject.ImageToVideoRenderRequest.model_json_schema()["properties"]
    assert "source_element_id" in properties
    assert "source_path" not in properties
    assert "workflow_name" not in properties
    assert "model" not in properties
    assert "provider_url" not in properties
    assert "variables" not in properties


def test_rights_confirmation_is_required_before_project_lookup(monkeypatch):
    monkeypatch.setattr(
        subject,
        "_resolve_project_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("project lookup must not run")),
    )
    body = subject.ImageToVideoRenderRequest(
        source_element_id="el-1",
        instruction="Slow cinematic camera push",
        rights_confirmed=False,
    )
    with pytest.raises(HTTPException) as exc:
        subject.render_project_image_to_video("project-a", body, _request())
    assert exc.value.status_code == 400


def test_project_image_resolver_rejects_parent_traversal(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    store = CreativeProjectStore(project)
    store.initialize(project_name="project-a", title="Project A")
    element = CreativeElement(
        kind="image",
        label="Unsafe image",
        status="ready",
        source_type="uploaded",
        source_ref="../outside.png",
    )
    store.add_element(element)
    monkeypatch.setattr(subject, "_project_dir", lambda _project_name: project)

    with pytest.raises(HTTPException) as exc:
        subject._resolve_project_image("project-a", element.id)
    assert exc.value.status_code == 400


def test_image_to_video_uses_server_renderer_token_and_preserves_boundaries(tmp_path, monkeypatch):
    store, element, source = _ready_image_project(tmp_path)
    monkeypatch.setattr(subject, "_project_dir", lambda _project_name: tmp_path)
    monkeypatch.setenv("AURA_COMFYUI_IMAGE_TO_VIDEO_WORKFLOW", "operator-i2v.json")

    seen = {}

    class FakeRenderer:
        configured = True
        workflow_name = "default-video.json"

        def upload_image_input(self, source_path):
            seen["source_path"] = source_path
            return RendererInput(
                name="aura_randomized.png",
                subfolder="",
                type="input",
                workflow_value="aura_randomized.png",
            )

        def submit(self, variables):
            seen["variables"] = dict(variables)
            return RendererSubmission(
                kind="video",
                prompt_id="prompt-1",
                client_id="client-1",
                workflow_name=self.workflow_name,
            )

    fake_renderer = FakeRenderer()
    monkeypatch.setattr(subject, "renderer_for", lambda _kind: fake_renderer)
    monkeypatch.setattr(
        subject.creative_render_resource_store,
        "reserve",
        lambda **kwargs: {"reservation_id": "reservation-1", "units": 1},
    )
    monkeypatch.setattr(
        subject.creative_render_resource_store,
        "cancel",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("successful render must not cancel")),
    )
    monkeypatch.setattr(subject, "_free_video_charge", lambda *args, **kwargs: None)

    body = subject.ImageToVideoRenderRequest(
        source_element_id=element.id,
        instruction="Animate the clouds while preserving the foreground subject",
        rights_confirmed=True,
        width=1280,
        height=720,
        frames=96,
        fps=24,
        seed=42,
    )
    response = subject.render_project_image_to_video("project-a", body, _request("pro"))

    assert fake_renderer.workflow_name == "operator-i2v.json"
    assert seen["source_path"] == source
    assert seen["variables"]["source_image"] == "aura_randomized.png"
    assert seen["variables"]["prompt"] == body.instruction
    assert "source.png" not in seen["variables"].values()
    assert response["source"]["project_owned"] is True
    assert response["source"]["raw_filesystem_path_exposed"] is False
    assert response["source"]["client_renderer_filename_accepted"] is False
    assert response["source"]["client_workflow_selection_accepted"] is False
    assert response["grants_esp_role_or_permission"] is False
    assert response["alters_billing_or_membership"] is False
    serialized = json.dumps(response)
    assert str(tmp_path) not in serialized

    manifest = store.load()
    directive = next(item for item in manifest.directives if item.id == response["directive"]["id"])
    assert directive.target_kind == "video"
    assert directive.target_element_ids == [element.id]
    assert directive.metadata["image_to_video"]["source_element_id"] == element.id
    assert "source_ref" not in directive.metadata["image_to_video"]


def test_renderer_input_token_rejects_traversal():
    from aura_music_studio.creative_renderers import ComfyUIRenderer

    assert ComfyUIRenderer._renderer_input_value("safe.png", "inputs/session") == "inputs/session/safe.png"
    with pytest.raises(ValueError):
        ComfyUIRenderer._renderer_input_value("safe.png", "../escape")
    with pytest.raises(ValueError):
        ComfyUIRenderer._renderer_input_value("../escape.png", "")
