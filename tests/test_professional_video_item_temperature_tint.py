from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_compositor import AdvancedVideoCompositor


def _sample_rgb(video: Path, x: int = 80, y: int = 45) -> tuple[int, int, int]:
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


def _video_project(tmp_path: Path):
    project = tmp_path / "TemperatureTintVideo"
    store = ProfessionalEditorStore(project)
    store.initialize("TemperatureTintVideo")
    sequence = store.create_sequence(
        kind="video",
        name="Temperature tint video",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = project / "sources" / "neutral.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real item colour tests")
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
            "color=c=0x808080:s=160x90:r=10:d=1",
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
        name="Neutral video",
        source_ref="sources/neutral.mp4",
        duration=1.0,
    )
    return project, store, sequence, item


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real item temperature/tint test")
def test_video_clip_temperature_and_tint_change_real_encoded_pixels(tmp_path):
    project, store, sequence, item = _video_project(tmp_path)
    store.patch_item(item.id, {"color": {"temperature": 0.9, "tint": 0.55}})

    result = AdvancedVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    pixel = _sample_rgb(output)

    assert output.is_file() and output.stat().st_size > 0
    assert pixel[0] > pixel[2] + 18, pixel
    assert pixel[0] > pixel[1] + 12, pixel

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["supports_item_temperature_tint"] is True
    assert metadata["item_temperature_tint_range"] == [-1.0, 1.0]
    assert metadata["item_temperature_tint_preserve_lightness"] is True
    assert metadata["item_highlights_shadows_fail_closed"] is True
    assert metadata["source_media_mutated"] is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for image-layer temperature/tint test")
def test_image_layer_temperature_tint_uses_same_post_crop_item_colour_stage(tmp_path):
    project = tmp_path / "TemperatureTintImage"
    store = ProfessionalEditorStore(project)
    store.initialize("TemperatureTintImage")
    sequence = store.create_sequence(
        kind="video",
        name="Temperature tint image",
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
    )
    track = store.create_track(sequence.id, kind="image", name="Still")
    source = project / "sources" / "neutral.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (128, 128, 128)).save(source)
    item = store.create_item(
        track.id,
        kind="image_layer",
        name="Neutral still",
        source_ref="sources/neutral.png",
        duration=1.0,
    )
    store.patch_item(item.id, {"color": {"temperature": -0.85, "tint": -0.45}})

    result = AdvancedVideoCompositor(project).render_video_advanced(sequence.id)
    pixel = _sample_rgb(project / result.output_ref)

    assert pixel[2] > pixel[0] + 15, pixel
    assert pixel[1] > pixel[0] + 8, pixel


def test_nonzero_highlights_or_shadows_fail_closed_in_advanced_item_colour_stage(tmp_path):
    project, store, sequence, item = _video_project(tmp_path)
    store.patch_item(item.id, {"color": {"highlights": 0.25}})
    with pytest.raises(EditorRenderUnsupported, match="highlights/shadows"):
        AdvancedVideoCompositor(project).render_video_advanced(sequence.id)

    project2, store2, sequence2, item2 = _video_project(tmp_path / "shadow")
    store2.patch_item(item2.id, {"color": {"shadows": -0.25}})
    with pytest.raises(EditorRenderUnsupported, match="highlights/shadows"):
        AdvancedVideoCompositor(project2).render_video_advanced(sequence2.id)
