from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_render_api import router as render_router
from aura_music_studio.professional_editor_renderer import (
    EditorRenderError,
    EditorRenderUnsupported,
    ProfessionalEditorRenderer,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _image_project(tmp_path: Path):
    project = tmp_path / "project"
    source = project / "input" / "layer.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (24, 24), (255, 0, 0, 255)).save(source)
    store = ProfessionalEditorStore(project)
    store.initialize("render-test")
    sequence = store.create_sequence(kind="image", name="Artwork", width=64, height=64, fps=1, duration=1)
    track = store.create_track(sequence.id, kind="image", name="Layer 1", role="image_layer")
    item = store.create_item(track.id, kind="image_layer", name="Red square", source_ref="input/layer.png", duration=1)
    return project, source, store, sequence, track, item


def test_image_compositor_creates_real_export_without_mutating_source(tmp_path: Path):
    project, source, store, sequence, _track, item = _image_project(tmp_path)
    before = _sha(source)
    store.patch_item(
        item.id,
        {
            "transform": {"x": 8, "y": -4, "scale_x": 1.5, "scale_y": 1.5},
            "crop": {"left": 0.1, "right": 0.1},
            "color": {"brightness": -0.1, "saturation": 0.8},
            "opacity": 0.75,
        },
    )

    result = ProfessionalEditorRenderer(project).render_image(sequence.id, format="png")
    output = project / result.output_ref
    metadata = project / result.metadata_ref

    assert output.is_file() and output.stat().st_size > 0
    assert metadata.is_file()
    assert _sha(source) == before
    assert result.source_media_mutated is False
    assert result.sha256 == _sha(output)
    with Image.open(output) as rendered:
        assert rendered.size == (64, 64)
        assert rendered.mode == "RGBA"
        assert rendered.getbbox() is not None

    evidence = json.loads(metadata.read_text(encoding="utf-8"))
    assert evidence["source_media_mutated"] is False
    assert evidence["renderer"] == "pillow-rgba-compositor"
    assert evidence["source_refs"] == ["input/layer.png"]


def test_renderer_rejects_path_escape_without_rewriting_existing_source(tmp_path: Path):
    project, source, store, sequence, track, _item = _image_project(tmp_path)
    renderer = ProfessionalEditorRenderer(project)
    before = _sha(source)

    with pytest.raises(EditorRenderError):
        renderer.resolve_export("../outside.png")

    # Source references are immutable after item creation. Exercise the renderer boundary by
    # constructing a second item with an invalid project-relative source instead of weakening
    # the editor's patch allow-list just for this test.
    store.create_item(
        track.id,
        kind="image_layer",
        name="Escaped source",
        source_ref="../outside.png",
        duration=1,
    )
    with pytest.raises(EditorRenderError, match="outside the project"):
        renderer.render_image(sequence.id)

    assert source.is_file()
    assert _sha(source) == before


def test_advanced_state_is_never_silently_dropped(tmp_path: Path):
    project, source, store, sequence, _track, item = _image_project(tmp_path)
    before = _sha(source)
    renderer = ProfessionalEditorRenderer(project)

    store.add_mask(item.id, EditorMask(name="Subject mask", shape="ellipse"))
    assert renderer.advanced_state(store.public_state(), sequence.id)["advanced"] is True
    with pytest.raises(EditorRenderUnsupported, match="Masks, effects and keyframes"):
        renderer.render_image(sequence.id)

    assert _sha(source) == before


def test_image_export_formats_are_real_files(tmp_path: Path):
    project, _source, _store, sequence, _track, _item = _image_project(tmp_path)
    renderer = ProfessionalEditorRenderer(project)
    webp = renderer.render_image(sequence.id, format="webp", quality=80)
    jpeg = renderer.render_image(sequence.id, format="jpeg", quality=85)
    assert (project / webp.output_ref).read_bytes()[:4] == b"RIFF"
    assert (project / jpeg.output_ref).read_bytes()[:2] == b"\xff\xd8"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed in this test runtime")
def test_ffmpeg_video_compositor_produces_real_mp4_and_preserves_source(tmp_path: Path):
    project = tmp_path / "project"
    source = project / "input" / "source.mp4"
    source.parent.mkdir(parents=True)
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=10:d=0.6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=20,
    )
    before = _sha(source)

    store = ProfessionalEditorStore(project)
    store.initialize("video-render-test")
    sequence = store.create_sequence(kind="video", name="Video", width=64, height=64, fps=10, duration=0.5)
    track = store.create_track(sequence.id, kind="video", name="Picture", role="picture")
    item = store.create_item(track.id, kind="video_clip", name="Shot", source_ref="input/source.mp4", start=0, duration=0.5)
    store.patch_item(item.id, {"transform": {"x": 2, "scale_x": 0.9, "scale_y": 0.9}, "color": {"contrast": 1.1}})

    result = ProfessionalEditorRenderer(project).render_video(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 100
    assert output.suffix == ".mp4"
    assert result.renderer == "ffmpeg-filter-compositor"
    assert _sha(source) == before
    assert result.source_media_mutated is False


def test_editor_render_routes_are_declared():
    paths = {route.path for route in render_router.routes if hasattr(route, "path")}
    assert "/creative/projects/{project_name}/editor/sequences/{sequence_id}/render" in paths
    assert "/creative/projects/{project_name}/editor/exports/{filename}" in paths
