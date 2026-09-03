from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageChops

from aura_music_studio.professional_editor import EditorEffect, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_universal_scoped_visual_video_compositor import (
    UniversalVisualVideoCompositor,
    _universal_or_legacy_video_effect_filter,
    install_universal_video_effect_dispatch,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_source(project):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real scoped universal visual render test")
    source = project / "sources" / "track_source.mp4"
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
            "testsrc2=size=160x90:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    return source


def _project(tmp_path):
    project = tmp_path / "ScopedUniversalVisualProject"
    store = ProfessionalEditorStore(project)
    store.initialize("ScopedUniversalVisualProject")
    sequence = store.create_sequence(
        kind="video",
        name="Track visual effects",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = _make_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Source",
        source_ref="sources/track_source.mp4",
        start=0.0,
        duration=1.0,
    )
    return project, store, sequence, track, item, source


def _extract_frame(video, frame):
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
            str(frame),
        ],
        check=True,
    )


def test_scoped_dispatch_is_idempotent_and_keeps_legacy_filters():
    install_universal_video_effect_dispatch()
    install_universal_video_effect_dispatch()

    universal = _universal_or_legacy_video_effect_filter(
        {"type": "video.fx.blur", "parameters": {"radius": 3}}
    )
    legacy = _universal_or_legacy_video_effect_filter(
        {"type": "contrast", "parameters": {"factor": 1.2}}
    )

    assert universal == "gblur=sigma=3"
    assert legacy == "eq=contrast=1.200000"
    with pytest.raises(EditorRenderUnsupported, match="Unsupported universal video visual effect"):
        _universal_or_legacy_video_effect_filter({"type": "video.fx.future", "parameters": {}})


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real track effect render test")
def test_real_whole_track_universal_effect_executes_after_item_composition_without_source_mutation(tmp_path):
    project, store, sequence, track, _item, source = _project(tmp_path)
    before_sha = _sha256(source)

    baseline = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    baseline_path = project / baseline.output_ref

    store.add_effect(
        "track",
        track.id,
        EditorEffect(
            type="video.grade.basic",
            mix=1.0,
            parameters={
                "exposure": 1.2,
                "contrast": 0.25,
                "saturation": -0.3,
                "temperature": 0.35,
                "tint": 0.1,
            },
        ),
    )
    store.add_effect(
        "track",
        track.id,
        EditorEffect(type="video.fx.vignette", mix=0.55, parameters={"amount": 0.65, "feather": 0.45}),
    )

    result = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref

    assert output.is_file() and output.stat().st_size > 0
    assert output.suffix == ".mp4"
    assert result.renderer == "ffmpeg-universal-scoped-visual-video-compositor"
    assert _sha256(source) == before_sha

    baseline_frame = tmp_path / "baseline.png"
    effect_frame = tmp_path / "track_effect.png"
    _extract_frame(baseline_path, baseline_frame)
    _extract_frame(output, effect_frame)
    with Image.open(baseline_frame) as left, Image.open(effect_frame) as right:
        assert ImageChops.difference(left.convert("RGB"), right.convert("RGB")).getbbox() is not None

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["universal_visual_video_compositor"] is True
    assert metadata["universal_visual_effect_contracts_executed"] == [
        "video.fx.vignette",
        "video.grade.basic",
    ]
    assert metadata["universal_visual_effect_instances_executed"] == 2
    assert metadata["universal_visual_track_effect_instances_executed"] == 2
    assert metadata["universal_visual_effect_scopes_executed"] == ["track"]
    assert metadata["supported_universal_video_visual_effect_scopes"] == ["item", "track"]
    assert metadata["universal_visual_track_effects_fail_closed"] is False
    assert metadata["universal_visual_track_effects_applied_after_item_composition"] is True
    assert metadata["universal_visual_track_effects_applied_before_track_opacity_and_blend"] is True
    assert metadata["source_media_mutated"] is False
    assert metadata["source_refs"] == ["sources/track_source.mp4"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real scoped render test")
def test_item_and_track_universal_effects_share_one_non_destructive_render(tmp_path):
    project, store, sequence, track, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.add_effect(
        "item",
        item.id,
        EditorEffect(type="video.fx.chromatic_aberration", parameters={"offset_px": 3}),
    )
    store.add_effect(
        "track",
        track.id,
        EditorEffect(type="video.fx.blur", mix=0.35, parameters={"radius": 2.0}),
    )

    result = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    assert (project / result.output_ref).is_file()
    assert _sha256(source) == before_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["universal_visual_effect_contracts_executed"] == [
        "video.fx.blur",
        "video.fx.chromatic_aberration",
    ]
    assert metadata["universal_visual_effect_instances_executed"] == 2
    assert metadata["universal_visual_track_effect_instances_executed"] == 1
    assert metadata["universal_visual_effect_scopes_executed"] == ["item", "track"]
    assert metadata["universal_visual_transient_derivatives"] == 1
    assert metadata["source_media_mutated"] is False

    transient_parent = project / "work" / "editor_universal_visual_effects"
    assert not transient_parent.exists() or not any(transient_parent.iterdir())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for fail-closed scoped renderer test")
def test_track_unknown_namespaced_effect_and_keyframes_still_fail_closed(tmp_path):
    project, store, sequence, track, _item, _source = _project(tmp_path)
    store.add_effect("track", track.id, EditorEffect(type="video.fx.future_effect", parameters={}))
    with pytest.raises(EditorRenderUnsupported, match="video.fx.future_effect"):
        UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)

    project2, store2, sequence2, track2, _item2, _source2 = _project(tmp_path / "keyframed")
    store2.add_effect(
        "track",
        track2.id,
        EditorEffect(
            type="video.grade.basic",
            parameters={"exposure": 0.0},
            keyframes={
                "exposure": [
                    {"time": 0.0, "value": 0.0, "interpolation": "linear"},
                    {"time": 1.0, "value": 1.0, "interpolation": "linear"},
                ]
            },
        ),
    )
    with pytest.raises(EditorRenderUnsupported, match="Keyframed universal video effect"):
        UniversalVisualVideoCompositor(project2).render_video_advanced(sequence2.id)


def test_production_render_api_uses_scoped_universal_video_compositor():
    import inspect

    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "professional_universal_scoped_visual_video_compositor" in source
    assert "video_renderer = UniversalVisualVideoCompositor" in source
