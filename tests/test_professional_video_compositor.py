from __future__ import annotations

import json
import math
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_compositor import (
    AdvancedVideoCompositor,
    _atempo_filters,
    _keyframe_expr,
)


def _project(tmp_path):
    project = tmp_path / "VideoProject"
    store = ProfessionalEditorStore(project)
    store.initialize("VideoProject")
    sequence = store.create_sequence(kind="video", name="Promo", width=160, height=90, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="text", name="Titles")
    item = store.create_item(track.id, kind="text", name="A", start=0.0, duration=1.0)
    store.patch_item(item.id, {"text": {"content": "A", "size": 44, "color": "#ffffffff"}})
    return project, store, sequence, track, item


def _product_atempo(filters: list[str]) -> float:
    value = 1.0
    for token in filters:
        value *= float(token.split("=", 1)[1])
    return value


def test_atempo_factorization_covers_full_editor_speed_range():
    for speed in (0.05, 0.1, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0, 10.0, 20.0):
        filters = _atempo_filters(speed)
        assert all(0.5 <= float(token.split("=", 1)[1]) <= 2.0 for token in filters)
        assert math.isclose(_product_atempo(filters), speed, rel_tol=1e-7, abs_tol=1e-7)


def test_keyframe_expression_contains_hold_and_smooth_segments():
    expression = _keyframe_expr(
        [
            {"time": 0.0, "value": -20.0, "interpolation": "hold"},
            {"time": 0.5, "value": 0.0, "interpolation": "smooth"},
            {"time": 1.0, "value": 20.0, "interpolation": "linear"},
        ],
        0.0,
    )
    assert "if(lt(t" in expression
    assert "if(lte(t" in expression
    assert "3-2*" in expression


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real video compositor test")
def test_keyframed_text_layer_moves_in_real_mp4_and_metadata_is_truthful(tmp_path):
    project, store, sequence, _track, item = _project(tmp_path)
    store.set_item_keyframes(
        item.id,
        "transform.x",
        [
            {"time": 0.0, "value": -40.0, "interpolation": "linear"},
            {"time": 1.0, "value": 40.0, "interpolation": "linear"},
        ],
    )
    result = AdvancedVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-advanced-video-compositor"
    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["advanced_video_compositor"] is True
    assert metadata["supports_text_layers"] is True
    assert metadata["supports_reverse_video_audio"] is True
    assert metadata["supports_speed_correct_audio"] is True
    assert metadata["source_media_mutated"] is False

    # Sample comfortably inside the encoded timeline. Seeking to the exact last frame boundary
    # (for example 0.9s on a 1.0s/10fps stream) can legitimately yield no decoded frame depending
    # on muxer timestamp rounding, which tests seeking rather than the editor motion itself.
    frames = []
    for stamp in (0.15, 0.75):
        frame = tmp_path / f"frame_{stamp}.png"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(stamp), "-i", str(output), "-frames:v", "1", str(frame),
            ],
            check=True,
        )
        assert frame.is_file() and frame.stat().st_size > 0
        frames.append(frame)

    def bright_centroid(path):
        with Image.open(path) as opened:
            gray = opened.convert("L")
            width, height = gray.size
            weighted_x = 0.0
            total = 0.0
            pixels = gray.load()
            for y in range(height):
                for x in range(width):
                    value = max(0, pixels[x, y] - 20)
                    if value:
                        weighted_x += x * value
                        total += value
            assert total > 0
            return weighted_x / total

    assert bright_centroid(frames[1]) - bright_centroid(frames[0]) > 35.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for video validation")
def test_unimplemented_video_mask_still_fails_closed(tmp_path):
    project, store, sequence, _track, item = _project(tmp_path)
    store.add_mask(
        item.id,
        EditorMask(name="Not silently ignored", shape="rectangle", points=[(0.1, 0.1), (0.9, 0.9)]),
    )
    with pytest.raises(EditorRenderUnsupported, match="Video masks are not yet render-safe"):
        AdvancedVideoCompositor(project).render_video_advanced(sequence.id)
