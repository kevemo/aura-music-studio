from __future__ import annotations

import inspect
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_track_keyframe_authoring import set_track_keyframes
from aura_music_studio.professional_video_track_keyframe_universal_compositor import (
    TrackKeyframeUniversalVisualVideoCompositor,
    _track_opacity_keyframes,
)


def _set_track_keyframes(store: ProfessionalEditorStore, track_id: str, path: str, points: list[dict]) -> None:
    set_track_keyframes(store, track_id, path, points, actor="Wave 10 Renderer Test")


def _project(tmp_path: Path):
    project = tmp_path / "TrackOpacityKeyframeProject"
    store = ProfessionalEditorStore(project)
    store.initialize("TrackOpacityKeyframeProject")
    sequence = store.create_sequence(
        kind="video",
        name="Track opacity automation",
        width=160,
        height=90,
        fps=10.0,
        duration=2.0,
    )
    store.patch_sequence(sequence.id, {"background": "#0000ff"})
    track = store.create_track(sequence.id, kind="video", name="Automated track")
    source = project / "sources" / "red.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (255, 0, 0)).save(source)
    store.create_item(
        track.id,
        kind="image_layer",
        name="Red card",
        source_ref="sources/red.png",
        start=0.0,
        duration=2.0,
    )
    return project, store, sequence, track


def _sample_rgb(video: Path, time_value: float) -> tuple[int, int, int]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{time_value:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "crop=1:1:80:45,format=rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    assert len(completed.stdout) >= 3
    return tuple(completed.stdout[:3])


def test_track_opacity_keyframe_contract_accepts_namespaced_or_direct_path():
    direct = {
        "keyframes": {
            "opacity": [
                {"time": 0.0, "value": 0.0, "interpolation": "linear"},
                {"time": 1.0, "value": 1.0, "interpolation": "smooth"},
            ]
        }
    }
    namespaced = {"keyframes": {"track.opacity": [{"time": 0.0, "value": 0.5}]}}
    assert [row["value"] for row in _track_opacity_keyframes(direct)] == [0.0, 1.0]
    assert _track_opacity_keyframes(namespaced)[0]["value"] == 0.5


def test_track_opacity_keyframe_contract_rejects_ambiguous_or_unsupported_paths():
    with pytest.raises(EditorRenderUnsupported, match="one path"):
        _track_opacity_keyframes(
            {
                "keyframes": {
                    "opacity": [{"time": 0.0, "value": 0.2}],
                    "track.opacity": [{"time": 0.0, "value": 0.8}],
                }
            }
        )
    with pytest.raises(EditorRenderUnsupported, match="not render-safe"):
        _track_opacity_keyframes({"keyframes": {"transform.x": [{"time": 0.0, "value": 1.0}]}})
    with pytest.raises(EditorRenderUnsupported, match="between 0 and 1"):
        _track_opacity_keyframes({"keyframes": {"opacity": [{"time": 0.0, "value": 1.5}]}})


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real track-opacity keyframe render test")
def test_real_grouped_track_opacity_keyframes_change_visible_pixels_over_time(tmp_path):
    project, store, sequence, track = _project(tmp_path)
    _set_track_keyframes(
        store,
        track.id,
        "opacity",
        [
            {"time": 0.0, "value": 0.0, "interpolation": "linear"},
            {"time": 1.5, "value": 1.0, "interpolation": "linear"},
        ],
    )

    result = TrackKeyframeUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file()
    assert result.renderer == "ffmpeg-universal-track-opacity-keyframe-video-compositor"

    early = _sample_rgb(output, 0.10)
    late = _sample_rgb(output, 1.75)
    assert early[2] > early[0] + 80, early
    assert late[0] > late[2] + 80, late

    metadata = (project / result.metadata_ref).read_text(encoding="utf-8")
    assert '"professional_track_keyframe_compositor": true' in metadata
    assert '"track_opacity_keyframes_execute_in_sequence_time": true' in metadata
    assert '"unsupported_track_keyframes_fail_closed": true' in metadata
    assert '"keyframed_track_effects_fail_closed": false' in metadata
    assert '"supported_keyframed_track_effects_preserved": true' in metadata


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for fail-closed render test")
def test_unsupported_track_keyframe_path_fails_closed_before_export(tmp_path):
    project, store, sequence, track = _project(tmp_path)
    _set_track_keyframes(
        store,
        track.id,
        "transform.x",
        [{"time": 0.0, "value": 25.0, "interpolation": "linear"}],
    )
    with pytest.raises(EditorRenderUnsupported, match="not render-safe"):
        TrackKeyframeUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)


def test_production_render_api_routes_through_track_keyframe_compositor():
    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "professional_video_track_keyframe_compositor" in source
    assert "professional_video_track_keyframe_universal_compositor" in source
    assert "video_renderer = UniversalVisualVideoCompositor" in source
    assert "professional_keyframed_mask_video_compositor ->" in source
