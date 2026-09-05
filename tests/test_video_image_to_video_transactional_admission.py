from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.video_image_to_video as image_to_video


def _request(plan_id: str = "free"):
    member = SimpleNamespace(user_id="user-1", plan=SimpleNamespace(id=plan_id))
    return SimpleNamespace(state=SimpleNamespace(member=member))


def _body():
    return image_to_video.ImageToVideoRenderRequest(
        source_element_id="image-1",
        instruction="Animate the clouds slowly",
        rights_confirmed=True,
    )


def _resolved(tmp_path: Path, store):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "input.png"
    source.write_bytes(b"png")
    manifest = SimpleNamespace(project_name="demo", title="Demo")
    element = SimpleNamespace(id="image-1", kind="image")
    return project, store, manifest, element, source


class FakeRenderer:
    configured = True
    workflow_name = ""

    def upload_image_input(self, _source):
        return SimpleNamespace(
            workflow_value="opaque-token.png",
            name="opaque-token.png",
            subfolder="",
            type="input",
        )


def _common(monkeypatch, tmp_path: Path, store):
    monkeypatch.setenv("AURA_COMFYUI_IMAGE_TO_VIDEO_WORKFLOW", "operator-workflow.json")
    monkeypatch.setattr(image_to_video, "_member_identity", lambda _request: ("user-1", "free"))
    monkeypatch.setattr(
        image_to_video,
        "_resolve_project_image",
        lambda _project, _element: _resolved(tmp_path, store),
    )
    monkeypatch.setattr(image_to_video, "renderer_for", lambda _kind: FakeRenderer())


def test_resource_denial_happens_before_directive_persistence(monkeypatch, tmp_path: Path):
    class Store:
        added = False

        def add_directive(self, _directive):
            self.added = True

    store = Store()
    _common(monkeypatch, tmp_path, store)

    def deny(**_kwargs):
        raise PermissionError("render capacity exhausted")

    monkeypatch.setattr(image_to_video.creative_render_resource_store, "reserve", deny)

    with pytest.raises(HTTPException) as exc:
        image_to_video.render_project_image_to_video("demo", _body(), _request())

    assert exc.value.status_code == 429
    assert store.added is False


def test_directive_validation_failure_releases_reserved_capacity(monkeypatch, tmp_path: Path):
    class Store:
        def add_directive(self, _directive):
            raise ValueError("invalid directive")

    store = Store()
    _common(monkeypatch, tmp_path, store)
    cancelled = []
    monkeypatch.setattr(
        image_to_video.creative_render_resource_store,
        "reserve",
        lambda **_kwargs: {"reservation_id": "reservation-1"},
    )
    monkeypatch.setattr(
        image_to_video.creative_render_resource_store,
        "cancel",
        lambda reservation_id, *, user_id: cancelled.append((reservation_id, user_id)),
    )

    with pytest.raises(HTTPException) as exc:
        image_to_video.render_project_image_to_video("demo", _body(), _request())

    assert exc.value.status_code == 400
    assert cancelled == [("reservation-1", "user-1")]


def test_creation_coin_charge_failure_marks_directive_failed_and_releases_capacity(monkeypatch, tmp_path: Path):
    class Store:
        def __init__(self):
            self.added = []
            self.updates = []

        def add_directive(self, directive):
            self.added.append(directive.id)

        def update_directive(self, directive_id, **changes):
            self.updates.append((directive_id, changes))
            return None

    store = Store()
    _common(monkeypatch, tmp_path, store)
    cancelled = []
    monkeypatch.setattr(
        image_to_video.creative_render_resource_store,
        "reserve",
        lambda **_kwargs: {"reservation_id": "reservation-2"},
    )
    monkeypatch.setattr(
        image_to_video.creative_render_resource_store,
        "cancel",
        lambda reservation_id, *, user_id: cancelled.append((reservation_id, user_id)),
    )

    def fail_charge(*_args, **_kwargs):
        raise RuntimeError("Creation Coin charge rejected")

    monkeypatch.setattr(image_to_video, "_free_video_charge", fail_charge)

    with pytest.raises(RuntimeError, match="Creation Coin charge rejected"):
        image_to_video.render_project_image_to_video("demo", _body(), _request())

    assert len(store.added) == 1
    directive_id = store.added[0]
    assert cancelled == [("reservation-2", "user-1")]
    assert store.updates == [(directive_id, {"status": "failed"})]
