from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from aura_music_studio.professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_mask_effects_colour_compositor import (
    MaskEffectsColourUniversalVisualVideoCompositor,
    _sanitized_state_for_mask_effects_colour_validation,
)
from aura_music_studio.professional_video_mask_crop_compositor import MaskCropUniversalVisualVideoCompositor


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path: Path):
    project = tmp_path / "MaskEffectsColourProject"
    store = ProfessionalEditorStore(project)
    store.initialize("MaskEffectsColourProject")
    sequence = store.create_sequence(
        kind="video",
        name="Mask effects colour interoperability",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    store.patch_sequence(sequence.id, {"background": "#0000ff"})
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = project / "sources" / "red.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for Video Studio interoperability tests")
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
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Red source",
        source_ref="sources/red.mp4",
        duration=1.0,
    )
    return project, store, sequence, item, source


def _sample_rgb(video: Path, x: int, y: int = 45) -> tuple[int, int, int]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
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


def test_validator_copy_neutralizes_all_actually_rendered_colour_controls(tmp_path):
    _project_dir, store, sequence, item, _source = _project(tmp_path)
    store.add_mask(item.id, EditorMask(shape="rectangle", points=[(0.5, 0.0), (1.0, 1.0)]))
    store.add_effect("item", item.id, EditorEffect(type="grayscale"))
    store.patch_item(
        item.id,
        {"color": {"brightness": 0.25, "contrast": 1.2, "temperature": 0.3, "tint": -0.2}},
    )
    state = store.public_state()

    sanitized = _sanitized_state_for_mask_effects_colour_validation(state, sequence.id)
    original = next(row for row in state["branch"]["items"] if row["id"] == item.id)
    copied = next(row for row in sanitized["branch"]["items"] if row["id"] == item.id)

    assert original["color"]["brightness"] == 0.25
    assert original["color"]["contrast"] == 1.2
    assert original["color"]["temperature"] == 0.3
    assert original["color"]["tint"] == -0.2
    assert copied["color"]["brightness"] == 0.0
    assert copied["color"]["contrast"] == 1.0
    assert copied["color"]["temperature"] == 0.0
    assert copied["color"]["tint"] == 0.0
    assert copied["masks"] == original["masks"]
    assert copied["effects"] == original["effects"]


def test_validator_does_not_unlock_unrendered_highlight_shadow_paths(tmp_path):
    project_dir, store, sequence, item, _source = _project(tmp_path)
    store.add_mask(item.id, EditorMask(shape="rectangle", points=[(0.0, 0.0), (1.0, 1.0)]))
    store.add_effect("item", item.id, EditorEffect(type="contrast", parameters={"factor": 1.1}))
    store.patch_item(item.id, {"color": {"brightness": 0.2, "highlights": 0.3}})
    state = store.public_state()

    sanitized = _sanitized_state_for_mask_effects_colour_validation(state, sequence.id)
    copied = next(row for row in sanitized["branch"]["items"] if row["id"] == item.id)
    assert copied["color"]["brightness"] == 0.2
    assert copied["color"]["highlights"] == 0.3

    with pytest.raises(EditorRenderUnsupported, match="colour adjustments|highlights/shadows"):
        MaskEffectsColourUniversalVisualVideoCompositor(project_dir).render_video_advanced(sequence.id)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real mask/effect/colour render test")
def test_mask_effect_temperature_tint_execute_after_mask_without_mutating_source(tmp_path):
    project, store, sequence, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.add_mask(item.id, EditorMask(shape="rectangle", points=[(0.5, 0.0), (1.0, 1.0)]))
    store.add_effect("item", item.id, EditorEffect(type="grayscale"))
    store.patch_item(
        item.id,
        {"color": {"brightness": 0.12, "contrast": 1.05, "temperature": 0.8, "tint": 0.35}},
    )

    result = MaskEffectsColourUniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-universal-mask-effects-colour-video-compositor"
    assert _sha256(source) == before_sha

    outside = _sample_rgb(output, 30)
    inside = _sample_rgb(output, 120)
    assert _is_blue(outside), outside
    # The legacy grayscale effect runs before the authored mask derivative. Temperature/tint then
    # run in the item-colour stage, so the visible grayscale region becomes warm/magenta rather than
    # remaining neutral gray. This proves the colour controls were not silently ignored or moved
    # ahead of the effect/mask stage.
    assert inside[0] > inside[2] + 12, inside
    assert inside[0] > inside[1] + 12, inside

    metadata = (project / result.metadata_ref).read_text(encoding="utf-8")
    assert '"professional_mask_effects_colour_interoperability": true' in metadata
    assert '"professional_mask_effects_colour_items_executed": 1' in metadata
    assert '"temperature"' in metadata
    assert '"tint"' in metadata
    assert '"item_effects_applied_before_mask_alpha": true' in metadata
    assert '"mask_alpha_applied_before_item_colour": true' in metadata
    assert '"item_colour_applied_after_mask_derivative": true' in metadata
    assert '"supports_item_temperature_tint": true' in metadata
    assert '"mask_effects_colour_source_media_mutated": false' in metadata


def test_wave13_interoperability_compositor_preserves_wave11_lineage():
    assert issubclass(MaskEffectsColourUniversalVisualVideoCompositor, MaskCropUniversalVisualVideoCompositor)
