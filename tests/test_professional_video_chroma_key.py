from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_chroma_key_video_compositor import (
    CHROMA_KEY_EFFECT,
    UniversalVisualVideoCompositor,
    _alpha_aware_universal_visual_graph,
)
from aura_music_studio.professional_editor import EditorEffect, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_chroma_key import chroma_key_filter


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_green_source(project):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real chroma key tests")
    source = project / "sources" / "green_source.mp4"
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
            "color=c=0x00ff00:s=160x90:r=10:d=1,drawbox=x=50:y=20:w=60:h=50:color=red:t=fill",
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


def _project(tmp_path, name="ChromaKeyProject"):
    project = tmp_path / name
    store = ProfessionalEditorStore(project)
    store.initialize(name)
    sequence = store.create_sequence(
        kind="video",
        name="Green screen",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    store.patch_sequence(sequence.id, {"background": "#0000ff"})
    track = store.create_track(sequence.id, kind="video", name="Keyed foreground")
    source = _make_green_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Green foreground",
        source_ref="sources/green_source.mp4",
        start=0.0,
        duration=1.0,
    )
    return project, store, sequence, track, item, source


def _key_effect():
    return EditorEffect(
        type=CHROMA_KEY_EFFECT,
        mix=1.0,
        parameters={
            "screen": "green",
            "color": "#00ff00",
            "similarity": 0.24,
            "blend": 0.04,
            "despill": 0.55,
            "despill_expand": 0.08,
        },
    )


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


def _assert_keyed_pixels(frame):
    with Image.open(frame) as image:
        rgb = image.convert("RGB")
        corner = rgb.getpixel((10, 10))
        centre = rgb.getpixel((80, 45))
    # Green screen must reveal the blue sequence background at the corner.
    assert corner[2] > 145
    assert corner[2] > corner[1] + 55
    assert corner[2] > corner[0] + 55
    # The red foreground box must survive the key.
    assert centre[0] > 135
    assert centre[0] > centre[1] + 55
    assert centre[0] > centre[2] + 55


def test_chroma_filter_is_bounded_and_preserves_alpha_contract():
    compiled = chroma_key_filter(
        {
            "type": CHROMA_KEY_EFFECT,
            "parameters": {
                "screen": "green",
                "color": "#00ff00",
                "similarity": 0.2,
                "blend": 0.05,
                "despill": 0.6,
                "despill_expand": 0.1,
            },
        }
    )
    assert compiled.startswith("format=rgba,colorkey=")
    assert "color=0x00ff00" in compiled
    assert "similarity=0.2" in compiled
    assert "blend=0.05" in compiled
    assert "despill=type=green:mix=0.6:expand=0.1:alpha=0" in compiled
    assert compiled.endswith("format=rgba")

    custom = chroma_key_filter(
        {
            "type": CHROMA_KEY_EFFECT,
            "parameters": {"screen": "custom", "color": "0x12Ab34", "despill": 1.0},
        }
    )
    assert "color=0x12ab34" in custom
    assert "despill=" not in custom

    with pytest.raises(EditorRenderUnsupported, match="Chroma key color"):
        chroma_key_filter(
            {"type": CHROMA_KEY_EFFECT, "parameters": {"screen": "custom", "color": "not-a-color"}}
        )


def test_chroma_graph_retains_rgba_and_mix_automation_boundary():
    effect = {
        "type": CHROMA_KEY_EFFECT,
        "mix": 1.0,
        "parameters": {"screen": "green", "similarity": 0.2, "blend": 0.04},
    }
    graph, label, applied = _alpha_aware_universal_visual_graph(
        [effect],
        filter_time_variable="t",
        blend_time_variable="T",
    )
    assert label == "uvout"
    assert applied == [CHROMA_KEY_EFFECT]
    assert graph is not None
    assert "colorkey=" in graph
    assert graph.endswith("format=rgba[uvout]")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real chroma key render")
def test_real_item_chroma_key_preserves_transparency_through_grouped_composition(tmp_path):
    project, store, sequence, _track, item, source = _project(tmp_path, "ItemChroma")
    before_sha = _sha256(source)
    store.add_effect("item", item.id, _key_effect())

    result = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-universal-chroma-key-video-compositor"
    assert _sha256(source) == before_sha

    frame = tmp_path / "item-key.png"
    _extract_frame(output, frame)
    _assert_keyed_pixels(frame)

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert CHROMA_KEY_EFFECT in metadata["universal_visual_effect_contracts_executed"]
    assert "item" in metadata["universal_visual_effect_scopes_executed"]
    assert metadata["professional_chroma_key_compositor"] is True
    assert metadata["professional_chroma_key_alpha_preserved"] is True
    assert metadata["professional_chroma_key_source_media_mutated"] is False
    assert metadata["professional_chroma_key_alpha_derivatives"] >= 1
    assert metadata["source_media_mutated"] is False
    transient = project / "work" / "editor_universal_visual_effects"
    assert not transient.exists() or not any(transient.iterdir())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real track chroma key render")
def test_real_whole_track_chroma_key_runs_in_grouped_rgba_before_track_blend(tmp_path):
    project, store, sequence, track, _item, source = _project(tmp_path, "TrackChroma")
    before_sha = _sha256(source)
    store.add_effect("track", track.id, _key_effect())

    result = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert _sha256(source) == before_sha

    frame = tmp_path / "track-key.png"
    _extract_frame(output, frame)
    _assert_keyed_pixels(frame)

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert CHROMA_KEY_EFFECT in metadata["universal_visual_effect_contracts_executed"]
    assert "track" in metadata["universal_visual_effect_scopes_executed"]
    assert metadata["professional_chroma_key_track_scope_uses_grouped_rgba"] is True
    assert metadata["professional_chroma_key_alpha_derivatives"] == 0
    assert metadata["universal_visual_track_effects_applied_before_track_opacity_and_blend"] is True


def test_chroma_parameter_keyframes_remain_fail_closed_but_mix_automation_is_registered():
    import aura_music_studio.professional_universal_scoped_visual_video_compositor as scoped

    assert CHROMA_KEY_EFFECT in scoped.SUPPORTED_UNIVERSAL_VIDEO_EFFECTS
    assert scoped._ANIMATABLE_UNIVERSAL_PARAMETERS[CHROMA_KEY_EFFECT] == frozenset()
    with pytest.raises(EditorRenderUnsupported, match="keyframe path is not yet render-safe"):
        scoped._animated_universal_visual_filter(
            {
                "type": CHROMA_KEY_EFFECT,
                "parameters": {"screen": "green"},
                "keyframes": {
                    "similarity": [
                        {"time": 0.0, "value": 0.1},
                        {"time": 1.0, "value": 0.4},
                    ]
                },
            },
            time_variable="t",
        )


def test_production_render_api_uses_chroma_capable_scoped_compositor():
    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "professional_chroma_key_video_compositor" in source
    assert "video_renderer = UniversalVisualVideoCompositor" in source
