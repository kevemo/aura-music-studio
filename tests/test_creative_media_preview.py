from __future__ import annotations

import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.creative_media_preview import MEDIA_PREVIEW_SCRIPT, resolve_element_media, router
from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id


def _project_with_media(tmp_path, monkeypatch, user_id="preview-user"):
    root = (tmp_path / "projects").resolve()
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tenant_storage, "ROOT", root)
    token = set_current_user_id(user_id)
    project = tenant_storage.project_path("visual-project", must_exist=False)
    project.mkdir(parents=True, exist_ok=True)
    store = CreativeProjectStore(project)
    store.initialize(project_name="visual-project", title="Visual Project", project_intent="Preview outputs")
    media = project / "output" / "creative" / "image" / "directive-1" / "01_cover.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"\x89PNG\r\n\x1a\npreview-test")
    element = CreativeElement(
        kind="image",
        label="Cover output",
        status="ready",
        source_type="generated",
        source_ref=media.relative_to(project).as_posix(),
    )
    store.add_element(element)
    return token, project, store, element, media


def test_element_media_resolves_only_manifest_registered_tenant_file(tmp_path, monkeypatch):
    token, project, store, element, media = _project_with_media(tmp_path, monkeypatch)
    try:
        path, media_type, public = resolve_element_media("visual-project", element.id)
        assert path == media.resolve()
        assert media_type == "image/png"
        assert public["id"] == element.id

        bad = CreativeElement(
            kind="image",
            label="Escaping path",
            status="ready",
            source_type="generated",
            source_ref="../outside.png",
        )
        store.add_element(bad)
        (project.parent / "outside.png").write_bytes(b"bad")
        with pytest.raises(ValueError):
            resolve_element_media("visual-project", bad.id)
    finally:
        reset_current_user_id(token)


def test_element_media_cannot_cross_member_project_root(tmp_path, monkeypatch):
    token, _project, _store, element, _media = _project_with_media(tmp_path, monkeypatch, user_id="member-a")
    reset_current_user_id(token)
    other = set_current_user_id("member-b")
    try:
        with pytest.raises(FileNotFoundError):
            resolve_element_media("visual-project", element.id)
    finally:
        reset_current_user_id(other)


def test_media_preview_surface_uses_element_ids_and_discloses_path_boundary():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/creative/projects/{project_name}/elements/{element_id}/media" in paths
    assert "/creative/media-preview-ui.js" in paths
    assert "Media Gallery" in MEDIA_PREVIEW_SCRIPT
    assert "elements/${encodeURIComponent(e.id)}/media" in MEDIA_PREVIEW_SCRIPT
    assert "arbitrary server paths are not accepted" in MEDIA_PREVIEW_SCRIPT
    assert "download=true" in MEDIA_PREVIEW_SCRIPT
