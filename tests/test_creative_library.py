from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio.creative_library import router, scan_creative_library
from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore
from aura_music_studio.plans import PLANS


def _member(plan_id: str):
    return SimpleNamespace(user_id=f"user-{plan_id}", plan=PLANS[plan_id])


def _project(tmp_path):
    project = tmp_path / "cosmic-song"
    store = CreativeProjectStore(project)
    store.initialize(project_name="cosmic-song", title="Cosmic Song")
    (project / "master.wav").write_bytes(b"RIFFdemo")
    (project / "cover.png").write_bytes(b"png-demo")
    (project / "movie.mp4").write_bytes(b"video-demo")
    store.add_element(CreativeElement(id="song-v1", kind="music", label="Master Song", role="master", status="ready", source_ref="master.wav"))
    store.add_element(CreativeElement(id="cover-v1", kind="image", label="Cover Art", role="cover", status="ready", source_ref="cover.png"))
    store.add_element(CreativeElement(id="video-v1", kind="video", label="Music Video", role="final video", status="ready", source_ref="movie.mp4"))
    return project


def test_free_library_allows_image_download_but_not_music_video(tmp_path):
    rows = scan_creative_library(_member("free"), [_project(tmp_path)])
    by_kind = {row["kind"]: row for row in rows}
    assert by_kind["image"]["download_allowed"] is True
    assert by_kind["image"]["download_url"].endswith("?download=true")
    assert by_kind["music"]["download_allowed"] is False
    assert by_kind["music"]["download_url"] is None
    assert by_kind["video"]["download_allowed"] is False
    assert by_kind["video"]["download_url"] is None


def test_basic_and_pro_library_allow_music_video_downloads(tmp_path):
    project = _project(tmp_path)
    for plan_id in ("base", "pro"):
        rows = scan_creative_library(_member(plan_id), [project])
        by_kind = {row["kind"]: row for row in rows}
        assert by_kind["music"]["download_allowed"] is True
        assert by_kind["video"]["download_allowed"] is True
        assert by_kind["image"]["download_allowed"] is True


def test_library_never_exposes_local_source_ref_or_filesystem_path(tmp_path):
    project = _project(tmp_path)
    rows = scan_creative_library(_member("pro"), [project])
    assert rows
    for row in rows:
        assert "source_ref" not in row
        assert "filesystem" not in row
        assert str(tmp_path) not in str(row)
        assert row["media_url"].startswith("/creative/projects/cosmic-song/elements/")


def test_library_ignores_path_escape_and_unsupported_media(tmp_path):
    project = tmp_path / "unsafe"
    store = CreativeProjectStore(project)
    store.initialize(project_name="unsafe", title="Unsafe")
    (tmp_path / "outside.mp3").write_bytes(b"outside")
    (project / "notes.exe").write_bytes(b"no")
    store.add_element(CreativeElement(id="escape", kind="audio", label="Escape", source_ref="../outside.mp3", status="ready"))
    store.add_element(CreativeElement(id="exe", kind="audio", label="Executable", source_ref="notes.exe", status="ready"))
    assert scan_creative_library(_member("pro"), [project]) == []


def test_creative_library_routes_are_member_creative_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/creative/library" in paths
    assert "/creative/api/library" in paths
    assert "/library" not in paths
