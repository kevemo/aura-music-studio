from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorEffect, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_effects_compositor import _SUPPORTED_VIDEO_EFFECTS
from aura_music_studio.professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor
from aura_music_studio.professional_video_track_compositor import (
    GroupedTrackVideoCompositor,
    _append_track_effects,
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


def _base_project(tmp_path, name="TrackEffects"):
    project = tmp_path / name
    store = ProfessionalEditorStore(project)
    store.initialize(name)
    sequence = store.create_sequence(
        kind="video",
        name="Track effects",
        width=64,
        height=64,
        fps=10.0,
        duration=1.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Picture")
    return project, store, sequence, track


def _effect(effect_type: str, *, mix: float = 1.0, enabled: bool = True, parameters=None, keyframes=None):
    return {
        "id": f"fx_{effect_type}",
        "type": effect_type,
        "enabled": enabled,
        "mix": mix,
        "parameters": parameters or {},
        "keyframes": keyframes or {},
        "metadata": {},
    }


def _editor_effect(effect_type: str, **kwargs) -> EditorEffect:
    return EditorEffect.model_validate(_effect(effect_type, **kwargs))


def test_track_effect_chain_preserves_authored_order():
    filters: list[str] = []
    current, applied = _append_track_effects(
        filters,
        input_label="track1items",
        effects=[
            _effect("grayscale"),
            _effect("brightness", parameters={"factor": 1.25}),
        ],
        track_index=1,
    )

    assert applied == 2
    assert current == "track1fx2"
    assert len(filters) == 2
    assert "[track1items]hue=s=0" in filters[0]
    assert "[track1fx1]eq=brightness=0.250000" in filters[1]


def test_disabled_effect_and_zero_mix_are_no_ops():
    filters: list[str] = []
    current, applied = _append_track_effects(
        filters,
        input_label="track2items",
        effects=[
            _effect("not-a-real-effect", enabled=False),
            _effect("contrast", mix=0.0, parameters={"factor": 2.0}),
        ],
        track_index=2,
    )

    assert current == "track2items"
    assert applied == 0
    assert filters == []


def test_active_unsupported_track_effect_is_rejected():
    with pytest.raises(EditorRenderUnsupported, match="Unsupported video item effect"):
        _append_track_effects(
            [],
            input_label="track3items",
            effects=[_effect("arbitrary-client-filter")],
            track_index=3,
        )


def test_keyframed_track_effect_is_rejected_until_automation_stage_exists():
    with pytest.raises(EditorRenderUnsupported, match="Keyframed video effect"):
        _append_track_effects(
            [],
            input_label="track4items",
            effects=[
                _effect(
                    "blur",
                    keyframes={"mix": [{"time": 0.0, "value": 1.0}]},
                )
            ],
            track_index=4,
        )


def test_renderer_stage_order_is_items_then_track_effects_then_opacity_then_blend():
    source = inspect.getsource(GroupedTrackVideoCompositor.render_video_advanced)
    effects_at = source.index("track_current, applied_effects = _append_track_effects(")
    opacity_at = source.index("track_opacity =", effects_at)
    blend_at = source.index("_append_full_frame_blend(", opacity_at)

    assert effects_at < opacity_at < blend_at


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for whole-track effects render test")
def test_grouped_unified_renderer_applies_real_track_effect_and_records_truthful_metadata(tmp_path):
    project, store, sequence, track = _base_project(tmp_path, "RealTrackEffect")
    source = _write_png(project / "sources" / "colour.png", (210, 70, 25, 255))
    source_sha = _sha256(source)
    store.create_item(
        track.id,
        kind="image_layer",
        name="Colour",
        source_ref="sources/colour.png",
        start=0.0,
        duration=1.0,
    )
    store.add_effect("track", track.id, _editor_effect("grayscale"))

    result = GroupedUnifiedAdvancedVideoCompositor(project).render_video_advanced(sequence.id)
    pixel = _extract_frame(project / result.output_ref, tmp_path / "track_effect_frame.png")

    assert max(pixel) - min(pixel) <= 8
    assert _sha256(source) == source_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["grouped_track_compositor"] is True
    assert metadata["grouped_unified_advanced_video_compositor"] is True
    assert metadata["supports_track_effects"] == sorted(_SUPPORTED_VIDEO_EFFECTS)
    assert metadata["track_effects_applied"] == 1
    assert metadata["track_effects_applied_before_opacity_and_blend"] is True
    assert metadata["track_opacity_applied_after_track_effects"] is True
    assert metadata["track_effects_fail_closed"] is False
    assert metadata["keyframed_track_effects_fail_closed"] is True
    assert metadata["track_keyframes_fail_closed"] is True
    assert metadata["source_media_mutated"] is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for whole-track effects blend-order test")
def test_track_effect_runs_before_whole_track_blend(tmp_path):
    project = tmp_path / "TrackEffectBlendOrder"
    store = ProfessionalEditorStore(project)
    store.initialize("TrackEffectBlendOrder")
    sequence = store.create_sequence(
        kind="video",
        name="Effect then blend",
        width=64,
        height=64,
        fps=10.0,
        duration=1.0,
    )
    bottom = store.create_track(sequence.id, kind="video", name="Bottom")
    top = store.create_track(sequence.id, kind="video", name="Top")
    _write_png(project / "sources" / "bottom.png", (200, 100, 50, 255))
    _write_png(project / "sources" / "top.png", (0, 200, 0, 255))
    store.create_item(
        bottom.id,
        kind="image_layer",
        name="Bottom",
        source_ref="sources/bottom.png",
        start=0.0,
        duration=1.0,
    )
    store.create_item(
        top.id,
        kind="image_layer",
        name="Top",
        source_ref="sources/top.png",
        start=0.0,
        duration=1.0,
    )
    store.add_effect("track", top.id, _editor_effect("grayscale"))
    store.patch_track(top.id, {"blend_mode": "multiply"})

    result = GroupedTrackVideoCompositor(project).render_video_advanced(sequence.id)
    pixel = _extract_frame(project / result.output_ref, tmp_path / "effect_before_blend.png")

    # If grayscale were incorrectly applied after Multiply, the result would be near-neutral gray.
    # Correct order produces a coloured Multiply result because the complete top track is grayscaled
    # first and that result is then blended against the lower track.
    assert pixel[0] - pixel[1] >= 20
    assert pixel[1] - pixel[2] >= 10
