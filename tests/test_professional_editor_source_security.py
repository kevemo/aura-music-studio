from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.creative_project import CreativeElement, CreativeManifest
from aura_music_studio import professional_editor_api as editor_api
from aura_music_studio.professional_editor_source_security import (
    normalize_project_source_ref,
    normalized_manifest_for_editor,
)


class _Plan:
    def has(self, _capability):
        return True


def _request():
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(plan=_Plan(), user={"display_name": "Boundary Test"})
        )
    )


def _manifest(*refs: str | None) -> CreativeManifest:
    return CreativeManifest(
        project_name="boundary-test",
        title="Boundary Test",
        elements=[
            CreativeElement(kind="video", label=f"Clip {index}", source_ref=ref)
            for index, ref in enumerate(refs, start=1)
        ],
    )


def test_normalize_project_source_ref_accepts_project_relative_paths(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    assert normalize_project_source_ref(project, "creative/renders/clip.mp4") == "creative/renders/clip.mp4"
    assert normalize_project_source_ref(project, r"creative\renders\clip.mp4") == "creative/renders/clip.mp4"
    assert normalize_project_source_ref(project, None) is None


@pytest.mark.parametrize(
    "source_ref",
    [
        "../outside.mp4",
        "creative/../../outside.mp4",
        "/etc/passwd",
        r"C:\Windows\system32\secret.mp4",
        "C:/Windows/system32/secret.mp4",
        r"\\server\share\clip.mp4",
        "https://example.invalid/clip.mp4",
        "file:///tmp/clip.mp4",
        "data:video/mp4;base64,AAAA",
        "creative/render\x00clip.mp4",
    ],
)
def test_normalize_project_source_ref_rejects_external_or_escaping_refs(tmp_path, source_ref):
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ValueError):
        normalize_project_source_ref(project, source_ref)


def test_normalize_project_source_ref_rejects_symlink_escape(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    link = project / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(ValueError, match="outside the project"):
        normalize_project_source_ref(project, "linked/clip.mp4")


def test_manifest_validation_is_all_or_nothing_and_does_not_mutate_input(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manifest = _manifest("creative/renders/good.mp4", "../outside.mp4")
    original = [element.source_ref for element in manifest.elements]

    with pytest.raises(ValueError):
        normalized_manifest_for_editor(project, manifest)

    assert [element.source_ref for element in manifest.elements] == original


def test_manifest_validation_returns_normalized_deep_copy(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    manifest = _manifest(r"creative\renders\clip.mp4")

    normalized = normalized_manifest_for_editor(project, manifest)

    assert normalized is not manifest
    assert normalized.elements[0] is not manifest.elements[0]
    assert normalized.elements[0].source_ref == "creative/renders/clip.mp4"
    assert manifest.elements[0].source_ref == r"creative\renders\clip.mp4"


def test_create_item_rejects_unsafe_source_before_store_mutation(tmp_path, monkeypatch):
    calls = []

    class Store:
        project_dir = tmp_path / "project"

        def create_item(self, *_args, **_kwargs):
            calls.append("create")
            raise AssertionError("unsafe source reached editor mutation")

    Store.project_dir.mkdir()
    monkeypatch.setattr(editor_api, "_store", lambda _project_name: Store())
    body = editor_api.ItemRequest(kind="video_clip", name="Unsafe", source_ref="../outside.mp4")

    with pytest.raises(HTTPException) as exc:
        editor_api.create_item("project", "track", body, _request())

    assert exc.value.status_code == 400
    assert calls == []


def test_sync_manifest_validates_every_source_before_timeline_mutation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    sync_calls = []

    class Store:
        project_dir = project

        def sync_creative_manifest(self, *_args, **_kwargs):
            sync_calls.append("sync")
            raise AssertionError("unsafe manifest reached timeline mutation")

    class Creative:
        def __init__(self, _project):
            pass

        def exists(self):
            return True

        def load(self):
            return _manifest("creative/renders/good.mp4", "../outside.mp4")

    monkeypatch.setattr(editor_api, "_store", lambda _project_name: Store())
    monkeypatch.setattr(editor_api, "CreativeProjectStore", Creative)

    with pytest.raises(HTTPException) as exc:
        editor_api.sync_manifest("project", _request())

    assert exc.value.status_code == 400
    assert sync_calls == []


def test_initialize_validates_manifest_before_editor_initialization(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    initialize_calls = []

    class Store:
        def __init__(self, _project):
            self.project_dir = project

        def initialize(self, _project_name):
            initialize_calls.append("initialize")
            raise AssertionError("unsafe manifest initialized editor state")

    class Creative:
        def __init__(self, _project):
            pass

        def exists(self):
            return True

        def load(self):
            return _manifest("creative/renders/good.mp4", "https://example.invalid/evil.mp4")

    monkeypatch.setattr(editor_api, "_project", lambda _project_name: project)
    monkeypatch.setattr(editor_api, "ProfessionalEditorStore", Store)
    monkeypatch.setattr(editor_api, "CreativeProjectStore", Creative)

    with pytest.raises(HTTPException) as exc:
        editor_api.initialize_editor(
            "project",
            editor_api.InitializeEditorRequest(sync_creative_manifest=True),
            _request(),
        )

    assert exc.value.status_code == 400
    assert initialize_calls == []
