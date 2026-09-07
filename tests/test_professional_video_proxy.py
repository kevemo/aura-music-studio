from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio import professional_video_proxy as proxy_module
from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import install_professional_editor_patch_guard
from aura_music_studio.professional_video_proxy import ProfessionalVideoProxyService, VideoProxyError


def _video_project(tmp_path: Path):
    project = (tmp_path / "project").resolve()
    project.mkdir()
    source = project / "input" / "camera-master.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-quality-video")

    store = ProfessionalEditorStore(project)
    store.initialize("proxy-test")
    sequence = store.create_sequence(
        kind="video",
        name="Long Form Edit",
        width=3840,
        height=2160,
        fps=24.0,
        duration=120.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Camera A", role="picture")
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Camera A master",
        source_ref="input/camera-master.mp4",
        duration=60.0,
    )
    return project, source, store, item


def _fake_ffmpeg(monkeypatch, calls: list[list[str]]):
    monkeypatch.setattr(proxy_module.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        assert "shell" not in kwargs
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        output = Path(argv[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"bounded-editing-proxy")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(proxy_module.subprocess, "run", fake_run)


def test_proxy_render_is_preview_only_and_final_source_ref_is_unchanged(tmp_path: Path, monkeypatch):
    project, source, store, item = _video_project(tmp_path)
    source_before = source.read_bytes()
    calls: list[list[str]] = []
    _fake_ffmpeg(monkeypatch, calls)

    service = ProfessionalVideoProxyService(project)
    result = service.render(item.id, "edit_540p", actor="Proxy Test")

    assert result["source_quality_preserved"] is True
    assert result["source_ref"] == "input/camera-master.mp4"
    assert result["proxy"]["preview_only"] is True
    assert result["proxy"]["final_render_uses_source"] is True
    assert result["proxy"]["proxy_ref"].startswith("work/editor_proxies/")
    assert result["proxy"]["proxy_ref"].endswith(".mp4")
    assert source.read_bytes() == source_before

    persisted = store.load_item(item.id)
    assert persisted.source_ref == "input/camera-master.mp4"
    assert persisted.metadata["proxy"]["proxy_ref"] == result["proxy"]["proxy_ref"]
    proxy_path = service.resolve_media(item.id)
    assert proxy_path.is_file()
    assert proxy_path.read_bytes() == b"bounded-editing-proxy"

    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "/usr/bin/ffmpeg"
    assert argv[argv.index("-vf") + 1] == "scale=-2:540"
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-f") + 1] == "mp4"
    assert "-nostdin" in argv
    assert "-y" in argv


def test_proxy_reuses_same_source_profile_and_purge_does_not_delete_source(tmp_path: Path, monkeypatch):
    project, source, store, item = _video_project(tmp_path)
    calls: list[list[str]] = []
    _fake_ffmpeg(monkeypatch, calls)
    service = ProfessionalVideoProxyService(project)

    first = service.render(item.id, "edit_720p", actor="Proxy Test")
    second = service.render(item.id, "edit_720p", actor="Proxy Test")
    assert len(calls) == 1
    assert first["proxy"]["proxy_ref"] == second["proxy"]["proxy_ref"]
    assert second["proxy"]["reused_existing"] is True

    result = service.purge(item.id, actor="Proxy Test")
    assert result["available"] is False
    assert result["proxy_file_deleted"] is True
    assert source.is_file()
    assert store.load_item(item.id).source_ref == "input/camera-master.mp4"
    assert store.load_item(item.id).metadata["proxy"] is None


def test_proxy_rejects_non_video_items_and_project_escape_symlinks(tmp_path: Path):
    project, _source, store, item = _video_project(tmp_path)
    sequence = next(row for row in store.public_state()["branch"]["sequences"] if row["kind"] == "video")
    image_track = store.create_track(sequence["id"], kind="image", name="Graphics")
    image = project / "input" / "poster.png"
    image.write_bytes(b"image")
    image_item = store.create_item(
        image_track.id,
        kind="image_layer",
        name="Poster",
        source_ref="input/poster.png",
        duration=5.0,
    )
    service = ProfessionalVideoProxyService(project)
    with pytest.raises(VideoProxyError, match="Only video clips"):
        service.render(image_item.id, "edit_540p", actor="Proxy Test")

    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    escaped = project / "input" / "escaped.mp4"
    try:
        escaped.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are unavailable on this test platform")
    escaped_item = store.create_item(
        next(row["id"] for row in store.public_state()["branch"]["tracks"] if item.id in row["item_ids"]),
        kind="video_clip",
        name="Escaping source",
        source_ref="input/escaped.mp4",
        duration=5.0,
    )
    with pytest.raises(VideoProxyError, match="outside the project"):
        service.render(escaped_item.id, "edit_540p", actor="Proxy Test")


def test_proxy_routes_mount_in_existing_professional_editor_family():
    install_professional_editor_patch_guard()
    paths = {getattr(route, "path", None) for route in professional_editor_router.routes}
    expected = {
        "/creative/projects/{project_name}/editor/items/{item_id}/proxy",
        "/creative/projects/{project_name}/editor/items/{item_id}/proxy/render",
        "/creative/projects/{project_name}/editor/items/{item_id}/proxy/media",
    }
    assert expected <= paths
