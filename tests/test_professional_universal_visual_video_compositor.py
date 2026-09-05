from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageChops, ImageStat

from aura_music_studio.professional_editor import EditorEffect, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_universal_visual_video_compositor import (
    SUPPORTED_UNIVERSAL_VIDEO_EFFECTS,
    UniversalVisualVideoCompositor,
    _universal_visual_filter,
    _universal_visual_graph,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_source(project):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real universal visual render test")
    source = project / "sources" / "universal_visual_source.mp4"
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
            "sine=frequency=523:sample_rate=48000:duration=1",
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
    project = tmp_path / "UniversalVisualProject"
    store = ProfessionalEditorStore(project)
    store.initialize("UniversalVisualProject")
    sequence = store.create_sequence(kind="video", name="Visual effects", width=160, height=90, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = _make_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Source",
        source_ref="sources/universal_visual_source.mp4",
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


def test_universal_visual_contract_filters_are_bounded_and_explicit():
    assert SUPPORTED_UNIVERSAL_VIDEO_EFFECTS == {
        "video.grade.basic",
        "video.fx.blur",
        "video.fx.vignette",
        "video.fx.film_grain",
        "video.fx.chromatic_aberration",
    }

    grade = _universal_visual_filter(
        {
            "type": "video.grade.basic",
            "parameters": {
                "exposure": 99,
                "contrast": 99,
                "saturation": -99,
                "temperature": 99,
                "tint": -99,
            },
        }
    )
    assert "eq=brightness=0.4:contrast=2:saturation=0" in grade
    assert "colorbalance=" in grade
    assert "rm=0.24" in grade
    assert "gm=0.2" in grade
    assert "bm=-0.24" in grade

    assert _universal_visual_filter({"type": "video.fx.blur", "parameters": {"radius": 7}}) == "gblur=sigma=7"
    assert "mode=forward" in _universal_visual_filter(
        {"type": "video.fx.vignette", "parameters": {"amount": 0.6, "feather": 0.5}}
    )
    assert "mode=backward" in _universal_visual_filter(
        {"type": "video.fx.vignette", "parameters": {"amount": -0.6, "feather": 0.5}}
    )
    assert "noise=alls=" in _universal_visual_filter(
        {"type": "video.fx.film_grain", "parameters": {"amount": 0.5, "size": 1.5}}
    )
    assert _universal_visual_filter(
        {"type": "video.fx.chromatic_aberration", "parameters": {"offset_px": 4}}
    ) == "rgbashift=rh=4:bh=-4:edge=smear"

    with pytest.raises(EditorRenderUnsupported, match="Unsupported universal video visual effect"):
        _universal_visual_filter({"type": "video.fx.not_real", "parameters": {}})
    with pytest.raises(EditorRenderUnsupported, match="Keyframed universal video effect"):
        _universal_visual_filter(
            {
                "type": "video.grade.basic",
                "parameters": {},
                "keyframes": {"exposure": [{"time": 0, "value": 0}, {"time": 1, "value": 1}]},
            }
        )


def test_universal_visual_graph_preserves_wet_dry_mix_and_order():
    graph, label, applied = _universal_visual_graph(
        [
            {"type": "video.grade.basic", "mix": 1.0, "parameters": {"exposure": 0.5}},
            {"type": "video.fx.film_grain", "mix": 0.25, "parameters": {"amount": 0.3}},
        ]
    )
    assert graph is not None
    assert "colorbalance=" in graph
    assert "noise=alls=" in graph
    assert "split=2" in graph
    assert "blend=all_expr" in graph
    assert label == "uvout"
    assert applied == ["video.grade.basic", "video.fx.film_grain"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real universal visual render test")
def test_real_universal_visual_effects_render_to_mp4_without_mutating_source(tmp_path):
    project, store, sequence, _track, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.add_effect(
        "item",
        item.id,
        EditorEffect(
            type="video.grade.basic",
            parameters={"exposure": 1.5, "contrast": 0.2, "saturation": -0.35, "temperature": 0.4, "tint": 0.15},
        ),
    )
    store.add_effect(
        "item",
        item.id,
        EditorEffect(type="video.fx.film_grain", mix=0.35, parameters={"amount": 0.35, "size": 1.25}),
    )

    result = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert output.suffix == ".mp4"
    assert result.renderer == "ffmpeg-universal-visual-video-compositor"
    assert _sha256(source) == before_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["universal_visual_video_compositor"] is True
    assert metadata["universal_visual_effect_contracts_executed"] == [
        "video.fx.film_grain",
        "video.grade.basic",
    ]
    assert metadata["universal_visual_effect_instances_executed"] == 2
    assert metadata["universal_visual_transient_derivatives"] == 1
    assert metadata["universal_visual_transient_derivatives_ephemeral"] is True
    assert metadata["source_media_mutated"] is False
    assert metadata["source_refs"] == ["sources/universal_visual_source.mp4"]
    assert metadata["original_source_refs"] == ["sources/universal_visual_source.mp4"]

    transient_parent = project / "work" / "editor_universal_visual_effects"
    assert not transient_parent.exists() or not any(transient_parent.iterdir())

    source_frame = tmp_path / "source_frame.png"
    output_frame = tmp_path / "output_frame.png"
    _extract_frame(source, source_frame)
    _extract_frame(output, output_frame)
    with Image.open(source_frame) as left, Image.open(output_frame) as right:
        left_rgb = left.convert("RGB")
        right_rgb = right.convert("RGB")
        assert ImageChops.difference(left_rgb, right_rgb).getbbox() is not None
        left_mean = sum(ImageStat.Stat(left_rgb).mean) / 3.0
        right_mean = sum(ImageStat.Stat(right_rgb).mean) / 3.0
    assert abs(left_mean - right_mean) > 1.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for fail-closed renderer test")
def test_universal_visual_renderer_fails_closed_for_unknown_namespaced_effect(tmp_path):
    project, store, sequence, _track, item, _source = _project(tmp_path)
    store.add_effect("item", item.id, EditorEffect(type="video.fx.future_effect", parameters={}))
    with pytest.raises(EditorRenderUnsupported, match="video.fx.future_effect"):
        UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for fail-closed renderer test")
def test_universal_visual_renderer_keeps_whole_track_contracts_fail_closed(tmp_path):
    project, store, sequence, track, _item, _source = _project(tmp_path)
    store.add_effect("track", track.id, EditorEffect(type="video.grade.basic", parameters={"exposure": 1}))
    with pytest.raises(EditorRenderUnsupported, match="item-local"):
        UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)


def test_editor_render_api_routes_video_exports_through_universal_visual_compositor():
    import inspect

    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "UniversalVisualVideoCompositor" in source
    assert "video_renderer = UniversalVisualVideoCompositor" in source
