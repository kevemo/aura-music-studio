from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_render_api import _sequence_has_non_normal_item_blend
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_compositor import (
    _SUPPORTED_VIDEO_ITEM_BLEND_MODES,
    AdvancedVideoCompositor,
    _ffmpeg_blend_mode,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_png(path, size, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, colour).save(path, format="PNG")
    return path


def _blend_project(tmp_path):
    project = tmp_path / "BlendVideoProject"
    store = ProfessionalEditorStore(project)
    store.initialize("BlendVideoProject")
    sequence = store.create_sequence(
        kind="video",
        name="Blend",
        width=64,
        height=64,
        fps=10.0,
        duration=1.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Picture")

    bottom_source = _write_png(project / "sources" / "bottom.png", (64, 64), (200, 100, 50, 255))
    top_source = _write_png(project / "sources" / "top.png", (24, 24), (128, 255, 128, 255))

    store.create_item(
        track.id,
        kind="image_layer",
        name="Bottom",
        source_ref="sources/bottom.png",
        start=0.0,
        duration=1.0,
    )
    top = store.create_item(
        track.id,
        kind="image_layer",
        name="Top multiply",
        source_ref="sources/top.png",
        start=0.0,
        duration=1.0,
    )
    store.patch_item(top.id, {"blend_mode": "multiply"})
    return project, store, sequence, track, bottom_source, top_source


def test_video_item_blend_mode_contract_matches_editor_model():
    assert _SUPPORTED_VIDEO_ITEM_BLEND_MODES == {
        "normal",
        "multiply",
        "screen",
        "overlay",
        "soft_light",
        "hard_light",
        "darken",
        "lighten",
        "difference",
    }
    assert _ffmpeg_blend_mode("soft_light") == "softlight"
    assert _ffmpeg_blend_mode("hard_light") == "hardlight"
    assert _ffmpeg_blend_mode("multiply") == "multiply"
    with pytest.raises(EditorRenderUnsupported, match="not render-safe"):
        _ffmpeg_blend_mode("colour_dodge")


def test_render_selector_detects_non_normal_item_blend(tmp_path):
    _project, store, sequence, _track, _bottom, _top = _blend_project(tmp_path)
    assert _sequence_has_non_normal_item_blend(store.public_state(), sequence.id) is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real blend-mode render test")
def test_multiply_blend_respects_position_alpha_and_source_integrity(tmp_path):
    project, _store, sequence, _track, bottom_source, top_source = _blend_project(tmp_path)
    bottom_sha = _sha256(bottom_source)
    top_sha = _sha256(top_source)

    result = AdvancedVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert _sha256(bottom_source) == bottom_sha
    assert _sha256(top_source) == top_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["supports_item_blend_modes"] == sorted(_SUPPORTED_VIDEO_ITEM_BLEND_MODES)
    assert metadata["track_blend_modes_require_group_compositing"] is True
    assert metadata["source_media_mutated"] is False

    frame = tmp_path / "blend_frame.png"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.4",
            "-i",
            str(output),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
    )
    with Image.open(frame) as opened:
        rgb = opened.convert("RGB")
        inside = rgb.getpixel((32, 32))
        outside = rgb.getpixel((5, 5))

    # Multiply: ~200*128/255, 100*255/255, 50*128/255 => ~100,100,25.
    assert 70 <= inside[0] <= 130
    assert 75 <= inside[1] <= 125
    assert 5 <= inside[2] <= 55

    # Outside the 24x24 centered layer must remain the untouched bottom layer.
    assert 165 <= outside[0] <= 225
    assert 70 <= outside[1] <= 130
    assert 25 <= outside[2] <= 80


def test_non_normal_track_blend_stays_fail_closed_until_group_compositing_exists(tmp_path):
    project = tmp_path / "TrackBlendBoundary"
    store = ProfessionalEditorStore(project)
    store.initialize("TrackBlendBoundary")
    sequence = store.create_sequence(kind="video", name="Track blend", width=64, height=64, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")
    store.patch_track(track.id, {"blend_mode": "multiply"})

    with pytest.raises(EditorRenderUnsupported, match="grouped track compositing"):
        AdvancedVideoCompositor(project).render_video_advanced(sequence.id)
