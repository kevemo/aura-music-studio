from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageStat

from aura_music_studio.professional_editor import EditorEffect, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_effects_compositor import (
    VideoItemEffectsCompositor,
    _video_effect_filter,
    _video_effect_graph,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_colour_mono_source(project):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real video item-effects test")
    source = project / "sources" / "colour_mono.mp4"
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
            "-ac",
            "1",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    return source


def _editor_project(tmp_path):
    project = tmp_path / "VideoEffectsProject"
    store = ProfessionalEditorStore(project)
    store.initialize("VideoEffectsProject")
    sequence = store.create_sequence(kind="video", name="Effects", width=160, height=90, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = _make_colour_mono_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Colour clip",
        source_ref="sources/colour_mono.mp4",
        start=0.0,
        duration=1.0,
    )
    store.add_effect("item", item.id, EditorEffect(type="grayscale", mix=1.0))
    store.patch_item(item.id, {"audio": {"pan": 0.8}})
    return project, store, sequence, item, source


def test_video_effect_filter_is_explicit_and_partial_mix_builds_wet_dry_graph():
    assert _video_effect_filter({"type": "grayscale", "parameters": {}}) == "hue=s=0"
    graph, label = _video_effect_graph([{"type": "contrast", "mix": 0.25, "parameters": {"factor": 1.4}}])
    assert graph is not None
    assert "split=2" in graph
    assert "blend=all_expr" in graph
    assert label == "vout"

    with pytest.raises(EditorRenderUnsupported, match="Unsupported video item effect"):
        _video_effect_filter({"type": "vignette", "parameters": {}})
    with pytest.raises(EditorRenderUnsupported, match="Keyframed video effect"):
        _video_effect_filter(
            {
                "type": "grayscale",
                "parameters": {},
                "keyframes": {"mix": [{"time": 0.0, "value": 0.0}, {"time": 1.0, "value": 1.0}]},
            }
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real video item-effects test")
def test_real_video_effect_and_mono_pan_are_rendered_non_destructively(tmp_path):
    project, _store, sequence, _item, source = _editor_project(tmp_path)
    before_sha = _sha256(source)

    result = VideoItemEffectsCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-video-item-effects-compositor"
    assert _sha256(source) == before_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["video_item_effects_compositor"] is True
    assert metadata["mono_pan_stereo_normalization"] is True
    assert metadata["transient_derivatives"] == 1
    assert metadata["transient_derivatives_ephemeral"] is True
    assert metadata["source_media_mutated"] is False
    assert metadata["source_refs"] == ["sources/colour_mono.mp4"]
    assert metadata["original_source_refs"] == ["sources/colour_mono.mp4"]

    transient_parent = project / "work" / "editor_video_effects"
    assert not transient_parent.exists() or not any(transient_parent.iterdir())

    frame = tmp_path / "effect_frame.png"
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
        means = ImageStat.Stat(opened.convert("RGB")).mean
    assert max(means) - min(means) < 3.0

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert completed.stdout.strip() == "2"
