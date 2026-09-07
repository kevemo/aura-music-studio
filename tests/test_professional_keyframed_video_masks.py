from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_keyframed_mask_video_compositor import (
    KeyframedMaskUniversalVisualVideoCompositor,
    _mask_state_at_time,
    _sequence_time_for_mask_frame,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_red_source(project, *, name="red_source.mp4"):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real keyframed mask render tests")
    source = project / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=10:d=1",
            "-c:v",
            "libx264",
            "-crf",
            "10",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    return source


def _project(tmp_path, name="KeyframedMaskProject"):
    project = tmp_path / name
    store = ProfessionalEditorStore(project)
    store.initialize(name)
    sequence = store.create_sequence(
        kind="video",
        name="Roto mask",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    store.patch_sequence(sequence.id, {"background": "#0000ff"})
    track = store.create_track(sequence.id, kind="video", name="Foreground")
    source = _make_red_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Red foreground",
        source_ref="sources/red_source.mp4",
        start=0.0,
        duration=1.0,
    )
    return project, store, sequence, track, item, source


def _moving_rectangle_mask():
    return EditorMask(
        name="Moving rectangle",
        shape="rectangle",
        points=[(0.05, 0.20), (0.45, 0.80)],
        feather=0.0,
        expansion=0.0,
        keyframes={
            "points": [
                {
                    "time": 0.0,
                    "value": [[0.05, 0.20], [0.45, 0.80]],
                    "interpolation": "linear",
                },
                {
                    "time": 1.0,
                    "value": [[0.55, 0.20], [0.95, 0.80]],
                    "interpolation": "linear",
                },
            ],
            "opacity": [
                {"time": 0.0, "value": 1.0, "interpolation": "linear"},
                {"time": 1.0, "value": 1.0, "interpolation": "linear"},
            ],
        },
    )


def _extract_frame(video, output, seconds: float):
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )


def _pixel(frame, xy):
    with Image.open(frame) as image:
        return image.convert("RGB").getpixel(xy)


def _red(pixel) -> bool:
    r, g, b = pixel
    return r > 150 and r > g * 1.6 and r > b * 1.6


def _blue(pixel) -> bool:
    r, g, b = pixel
    return b > 120 and b > r * 1.4 and b > g * 1.4


def test_mask_point_and_numeric_keyframes_interpolate_in_sequence_time():
    mask = _moving_rectangle_mask().model_dump(mode="json")
    mask["keyframes"]["feather"] = [
        {"time": 0.0, "value": 0.0, "interpolation": "linear"},
        {"time": 1.0, "value": 20.0, "interpolation": "linear"},
    ]
    mask["keyframes"]["expansion"] = [
        {"time": 0.0, "value": -4.0, "interpolation": "smooth"},
        {"time": 1.0, "value": 4.0, "interpolation": "smooth"},
    ]

    state = _mask_state_at_time(mask, 0.5)
    assert state["points"][0] == pytest.approx((0.30, 0.20))
    assert state["points"][1] == pytest.approx((0.70, 0.80))
    assert state["opacity"] == pytest.approx(1.0)
    assert state["feather"] == pytest.approx(10.0)
    assert state["expansion"] == pytest.approx(0.0)
    assert state["keyframes"] == {}


def test_reverse_and_speed_mapping_keeps_mask_automation_on_authored_sequence_time():
    forward = {"start": 5.0, "duration": 2.0, "speed": 2.0, "reverse": False}
    reverse = {**forward, "reverse": True}

    assert _sequence_time_for_mask_frame(forward, 0.0) == pytest.approx(5.0)
    assert _sequence_time_for_mask_frame(forward, 2.0) == pytest.approx(6.0)
    assert _sequence_time_for_mask_frame(reverse, 0.0) == pytest.approx(7.0)
    assert _sequence_time_for_mask_frame(reverse, 2.0) == pytest.approx(6.0)


def test_interpolated_point_topology_changes_fail_closed_but_hold_can_change_topology():
    linear = EditorMask(
        shape="polygon",
        points=[(0.1, 0.1), (0.4, 0.1), (0.2, 0.4)],
        keyframes={
            "points": [
                {"time": 0.0, "value": [[0.1, 0.1], [0.4, 0.1], [0.2, 0.4]], "interpolation": "linear"},
                {
                    "time": 1.0,
                    "value": [[0.5, 0.1], [0.9, 0.1], [0.9, 0.5], [0.5, 0.5]],
                    "interpolation": "linear",
                },
            ]
        },
    ).model_dump(mode="json")
    with pytest.raises(EditorRenderUnsupported, match="same control-point count"):
        _mask_state_at_time(linear, 0.5)

    held = json.loads(json.dumps(linear))
    held["keyframes"]["points"][0]["interpolation"] = "hold"
    state = _mask_state_at_time(held, 0.5)
    assert len(state["points"]) == 3


def test_unsupported_mask_paths_and_tracking_remain_fail_closed(tmp_path):
    project, store, sequence, _track, item, _source = _project(tmp_path, "FailClosedMaskProject")
    store.add_mask(
        item.id,
        EditorMask(
            shape="rectangle",
            points=[(0.1, 0.1), (0.8, 0.8)],
            keyframes={
                "rotation": [
                    {"time": 0.0, "value": 0.0},
                    {"time": 1.0, "value": 45.0},
                ]
            },
        ),
    )
    with pytest.raises(EditorRenderUnsupported, match="Unsupported keyframed video mask path"):
        KeyframedMaskUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)

    project2, store2, sequence2, _track2, item2, _source2 = _project(tmp_path, "TrackedMaskProject")
    store2.add_mask(
        item2.id,
        EditorMask(
            shape="rectangle",
            points=[(0.1, 0.1), (0.8, 0.8)],
            tracking={"provider": "future_tracker", "track_id": "subject-1"},
        ),
    )
    with pytest.raises(EditorRenderUnsupported, match="Automatic/tracked video masks"):
        KeyframedMaskUniversalVisualVideoCompositor(project2).render_video_advanced(sequence2.id)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real keyframed mask render test")
def test_real_keyframed_rectangle_roto_moves_across_frame_and_keeps_source_immutable(tmp_path):
    project, store, sequence, _track, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.add_mask(item.id, _moving_rectangle_mask())

    result = KeyframedMaskUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-universal-keyframed-mask-video-compositor"
    assert _sha256(source) == before_sha

    early = tmp_path / "mask_early.png"
    late = tmp_path / "mask_late.png"
    _extract_frame(output, early, 0.1)
    _extract_frame(output, late, 0.8)

    assert _red(_pixel(early, (30, 45)))
    assert _blue(_pixel(early, (130, 45)))
    assert _blue(_pixel(late, (30, 45)))
    assert _red(_pixel(late, (130, 45)))

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["professional_keyframed_mask_compositor"] is True
    assert metadata["professional_keyframed_mask_instances_executed"] == 1
    assert metadata["professional_keyframed_mask_streamed_raw_gray_frames"] is True
    assert metadata["professional_keyframed_mask_sequence_time_alignment"] is True
    assert metadata["professional_keyframed_mask_reverse_speed_alignment"] is True
    assert metadata["professional_keyframed_mask_source_media_mutated"] is False
    assert metadata["automatic_mask_tracking_supported"] is False
    assert metadata["tracked_masks_fail_closed"] is True
    assert metadata["keyframed_masks_fail_closed"] is False

    transient_parent = project / "work" / "editor_video_grouped_effects"
    assert not transient_parent.exists() or not any(transient_parent.iterdir())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for chroma+mask alpha integration test")
def test_static_mask_multiplies_existing_chroma_alpha_instead_of_replacing_it(tmp_path):
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    project = tmp_path / "ChromaMaskIntegrationProject"
    store = ProfessionalEditorStore(project)
    store.initialize("ChromaMaskIntegrationProject")
    sequence = store.create_sequence(
        kind="video",
        name="Chroma plus mask",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    store.patch_sequence(sequence.id, {"background": "#0000ff"})
    track = store.create_track(sequence.id, kind="video", name="Foreground")
    source = project / "sources" / "green_box.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x00ff00:s=160x90:r=10:d=1,drawbox=x=45:y=20:w=70:h=50:color=red:t=fill",
            "-c:v",
            "libx264",
            "-crf",
            "10",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Green foreground",
        source_ref="sources/green_box.mp4",
        duration=1.0,
    )
    store.add_effect(
        "item",
        item.id,
        EditorEffect(
            type="video.key.chroma",
            parameters={"screen": "green", "color": "#00ff00", "similarity": 0.24, "blend": 0.03, "despill": 0.4},
        ),
    )
    store.add_mask(
        item.id,
        EditorMask(shape="rectangle", points=[(0.35, 0.1), (0.70, 0.9)]),
    )

    result = KeyframedMaskUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    frame = tmp_path / "chroma_mask.png"
    _extract_frame(project / result.output_ref, frame, 0.4)

    assert _blue(_pixel(frame, (10, 45)))
    assert _red(_pixel(frame, (90, 45)))
    assert _blue(_pixel(frame, (120, 45)))


def test_production_render_api_routes_through_keyframed_mask_compositor():
    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "professional_keyframed_mask_video_compositor" in source
    assert "video_renderer = UniversalVisualVideoCompositor" in source
