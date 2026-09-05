from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_effects_compositor import _StateProxy
from aura_music_studio.professional_video_track_compositor import (
    GroupedTrackVideoCompositor,
    _SUPPORTED_VIDEO_TRACK_BLEND_MODES,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_png(path, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 64), colour).save(path, format="PNG")
    return path


def _extract_frame(video, output):
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
            str(video),
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )
    with Image.open(output) as opened:
        return opened.convert("RGB").getpixel((32, 32))


def _base_project(tmp_path, name="GroupedTrackVideo"):
    project = tmp_path / name
    store = ProfessionalEditorStore(project)
    store.initialize(name)
    sequence = store.create_sequence(
        kind="video",
        name="Grouped",
        width=64,
        height=64,
        fps=10.0,
        duration=1.0,
    )
    return project, store, sequence


def test_track_blend_contract_matches_supported_item_blends():
    assert _SUPPORTED_VIDEO_TRACK_BLEND_MODES == {
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


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for grouped track render test")
def test_track_multiply_blends_completed_top_track_against_lower_track(tmp_path):
    project, store, sequence = _base_project(tmp_path, "TrackMultiply")
    bottom_track = store.create_track(sequence.id, kind="video", name="Bottom")
    top_track = store.create_track(sequence.id, kind="video", name="Top")
    bottom_source = _write_png(project / "sources" / "bottom.png", (200, 100, 50, 255))
    top_source = _write_png(project / "sources" / "top.png", (128, 255, 128, 255))
    bottom_sha = _sha256(bottom_source)
    top_sha = _sha256(top_source)

    store.create_item(
        bottom_track.id,
        kind="image_layer",
        name="Bottom",
        source_ref="sources/bottom.png",
        start=0.0,
        duration=1.0,
    )
    store.create_item(
        top_track.id,
        kind="image_layer",
        name="Top",
        source_ref="sources/top.png",
        start=0.0,
        duration=1.0,
    )
    store.patch_track(top_track.id, {"blend_mode": "multiply"})

    result = GroupedTrackVideoCompositor(project).render_video_advanced(sequence.id)
    pixel = _extract_frame(project / result.output_ref, tmp_path / "track_multiply.png")

    assert 70 <= pixel[0] <= 130
    assert 75 <= pixel[1] <= 125
    assert 5 <= pixel[2] <= 55
    assert _sha256(bottom_source) == bottom_sha
    assert _sha256(top_source) == top_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["grouped_track_compositor"] is True
    assert metadata["supports_track_blend_modes"] == sorted(_SUPPORTED_VIDEO_TRACK_BLEND_MODES)
    assert metadata["track_opacity_applied_after_item_composition"] is True
    assert metadata["source_media_mutated"] is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for grouped track render test")
def test_track_opacity_is_applied_once_after_items_are_composited(tmp_path):
    project, store, sequence = _base_project(tmp_path, "TrackOpacity")
    track = store.create_track(sequence.id, kind="video", name="Group")
    _write_png(project / "sources" / "red.png", (200, 0, 0, 255))
    _write_png(project / "sources" / "green.png", (0, 200, 0, 255))

    store.create_item(
        track.id,
        kind="image_layer",
        name="Red underlay",
        source_ref="sources/red.png",
        start=0.0,
        duration=1.0,
    )
    store.create_item(
        track.id,
        kind="image_layer",
        name="Green cover",
        source_ref="sources/green.png",
        start=0.0,
        duration=1.0,
    )
    store.patch_track(track.id, {"opacity": 0.5})

    result = GroupedTrackVideoCompositor(project).render_video_advanced(sequence.id)
    pixel = _extract_frame(project / result.output_ref, tmp_path / "track_opacity.png")

    # Correct grouped semantics: top green fully replaces red inside the track, then the completed
    # track is faded once over black. Per-item track opacity would leak a visible red contribution.
    assert pixel[0] <= 20
    assert 70 <= pixel[1] <= 130
    assert pixel[2] <= 20


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for grouped track render test")
def test_non_normal_first_item_on_transparent_track_keeps_normal_coverage(tmp_path):
    project, store, sequence = _base_project(tmp_path, "TransparentTrackBlend")
    track = store.create_track(sequence.id, kind="video", name="Transparent group")
    _write_png(project / "sources" / "green.png", (40, 180, 80, 255))
    item = store.create_item(
        track.id,
        kind="image_layer",
        name="First multiply item",
        source_ref="sources/green.png",
        start=0.0,
        duration=1.0,
    )
    store.patch_item(item.id, {"blend_mode": "multiply"})

    result = GroupedTrackVideoCompositor(project).render_video_advanced(sequence.id)
    pixel = _extract_frame(project / result.output_ref, tmp_path / "transparent_first_blend.png")

    # With no covered backdrop inside the transparent track, Multiply must not multiply against
    # invisible black; the normal composite supplies the visible item coverage.
    assert 20 <= pixel[0] <= 70
    assert 145 <= pixel[1] <= 210
    assert 50 <= pixel[2] <= 115


def test_static_track_effects_render_but_track_keyframes_remain_fail_closed(tmp_path):
    project, store, sequence = _base_project(tmp_path, "TrackFailClosed")
    track = store.create_track(sequence.id, kind="video", name="Picture")

    state = store.public_state()
    compositor = GroupedTrackVideoCompositor(project)
    _sequences, tracks, _items = compositor._branch_maps(state)
    tracks[track.id]["effects"] = [
        {"id": "fx_test", "type": "blur", "enabled": True, "mix": 1.0, "params": {}}
    ]
    compositor.store = _StateProxy(state)  # type: ignore[assignment]
    compositor.render_video_advanced(sequence.id)

    state = store.public_state()
    compositor = GroupedTrackVideoCompositor(project)
    _sequences, tracks, _items = compositor._branch_maps(state)
    tracks[track.id]["keyframes"] = {"opacity": [{"time": 0.0, "value": 1.0}]}
    compositor.store = _StateProxy(state)  # type: ignore[assignment]
    with pytest.raises(EditorRenderUnsupported, match="track keyframes"):
        compositor.render_video_advanced(sequence.id)
