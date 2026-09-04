from __future__ import annotations

import hashlib
import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from aura_music_studio.professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_mask_crop_compositor import (
    MaskCropUniversalVisualVideoCompositor,
    _sanitized_state_for_mask_crop_validation,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_red_source(project: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real mask/crop render tests")
    source = project / "sources" / "red.mp4"
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
            "color=c=red:s=160x90:r=10:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    return source


def _project(tmp_path: Path):
    project = tmp_path / "MaskCropProject"
    store = ProfessionalEditorStore(project)
    store.initialize("MaskCropProject")
    sequence = store.create_sequence(
        kind="video",
        name="Mask crop interoperability",
        width=160,
        height=90,
        fps=10.0,
        duration=2.0,
    )
    store.patch_sequence(sequence.id, {"background": "#0000ff"})
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = _make_red_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Red source",
        source_ref="sources/red.mp4",
        start=0.0,
        duration=2.0,
    )
    return project, store, sequence, track, item, source


def _sample_rgb(video: Path, time_value: float, x: int, y: int = 45) -> tuple[int, int, int]:
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
            f"crop=1:1:{x}:{y},format=rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    assert len(completed.stdout) >= 3
    return tuple(completed.stdout[:3])


def _is_blue(pixel: tuple[int, int, int]) -> bool:
    return pixel[2] > pixel[0] + 80 and pixel[2] > pixel[1] + 80


def _is_red(pixel: tuple[int, int, int]) -> bool:
    return pixel[0] > pixel[2] + 80 and pixel[0] > pixel[1] + 80


def test_validation_adapter_removes_only_crop_from_masked_item_copy(tmp_path):
    _project_dir, store, sequence, _track, item, _source = _project(tmp_path)
    store.patch_item(item.id, {"crop": {"left": 0.25, "right": 0.1}})
    store.add_mask(item.id, EditorMask(shape="rectangle", points=[(0.5, 0.0), (1.0, 1.0)]))
    state = store.public_state()

    sanitized = _sanitized_state_for_mask_crop_validation(state, sequence.id)
    original_item = next(row for row in state["branch"]["items"] if row["id"] == item.id)
    sanitized_item = next(row for row in sanitized["branch"]["items"] if row["id"] == item.id)

    assert original_item["crop"]["left"] == 0.25
    assert original_item["crop"]["right"] == 0.1
    assert sanitized_item["crop"] == {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    assert sanitized_item["masks"] == original_item["masks"]
    assert sanitized_item["effects"] == original_item["effects"]
    assert sanitized_item["color"] == original_item["color"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real mask/crop render test")
def test_static_mask_and_crop_execute_in_one_non_destructive_render(tmp_path):
    project, store, sequence, _track, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.patch_item(item.id, {"crop": {"left": 0.25, "right": 0.0}})
    store.add_mask(
        item.id,
        EditorMask(shape="rectangle", points=[(0.5, 0.0), (1.0, 1.0)]),
    )

    result = MaskCropUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-universal-mask-crop-video-compositor"
    assert _sha256(source) == before_sha

    assert _is_blue(_sample_rgb(output, 0.5, 30))
    assert _is_red(_sample_rgb(output, 0.5, 100))
    assert _is_blue(_sample_rgb(output, 0.5, 150))

    metadata = (project / result.metadata_ref).read_text(encoding="utf-8")
    assert '"professional_mask_crop_interoperability": true' in metadata
    assert '"professional_mask_crop_items_executed": 1' in metadata
    assert '"mask_alpha_applied_before_crop": true' in metadata
    assert '"crop_applied_after_mask_derivative": true' in metadata
    assert '"mask_crop_source_media_mutated": false' in metadata
    assert '"mask_crop_fail_closed": false' in metadata


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real keyframed mask/crop render test")
def test_keyframed_mask_opacity_and_crop_execute_together(tmp_path):
    project, store, sequence, _track, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.patch_item(item.id, {"crop": {"left": 0.25, "right": 0.25}})
    store.add_mask(
        item.id,
        EditorMask(
            shape="rectangle",
            points=[(0.0, 0.0), (1.0, 1.0)],
            keyframes={
                "opacity": [
                    {"time": 0.0, "value": 0.0, "interpolation": "linear"},
                    {"time": 1.0, "value": 1.0, "interpolation": "linear"},
                ]
            },
        ),
    )

    result = MaskCropUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert _sha256(source) == before_sha

    early_center = _sample_rgb(output, 0.1, 80)
    late_center = _sample_rgb(output, 1.5, 80)
    assert _is_blue(early_center), early_center
    assert _is_red(late_center), late_center
    assert _is_blue(_sample_rgb(output, 1.5, 20))
    assert _is_blue(_sample_rgb(output, 1.5, 140))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for fail-closed mask tests")
def test_mask_crop_does_not_bypass_tracking_or_unsupported_colour_safety_boundaries(tmp_path):
    project, store, sequence, _track, item, _source = _project(tmp_path)
    store.patch_item(item.id, {"crop": {"left": 0.2}})
    store.add_mask(
        item.id,
        EditorMask(
            shape="rectangle",
            points=[(0.0, 0.0), (1.0, 1.0)],
            tracking={"provider": "not-a-real-tracker"},
        ),
    )
    with pytest.raises(EditorRenderUnsupported, match="Automatic/tracked video masks"):
        MaskCropUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)

    project2, store2, sequence2, _track2, item2, _source2 = _project(tmp_path / "effects-colour")
    store2.patch_item(
        item2.id,
        {
            "crop": {"left": 0.2},
            "color": {"highlights": 0.2},
        },
    )
    store2.add_mask(item2.id, EditorMask(shape="rectangle", points=[(0.0, 0.0), (1.0, 1.0)]))
    store2.add_effect(
        "item",
        item2.id,
        EditorEffect(type="contrast", parameters={"factor": 1.1}),
    )
    with pytest.raises(EditorRenderUnsupported, match="colour adjustments|highlights/shadows"):
        MaskCropUniversalVisualVideoCompositor(project2).render_video_advanced(sequence2.id)


def test_production_render_api_routes_through_mask_crop_compositor():
    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "professional_video_mask_effects_colour_compositor" in source
    assert "professional_video_mask_crop_compositor ->" in source
    assert "professional_video_track_keyframe_universal_compositor ->" in source
    assert "video_renderer = UniversalVisualVideoCompositor" in source
