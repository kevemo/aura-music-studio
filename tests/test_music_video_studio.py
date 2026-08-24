from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.models import RendererConfig
from aura_music_studio.plans import (
    BASIC_VIDEO_STUDIO,
    LYRIC_VIDEO,
    NEURAL_VIDEO,
    VIDEO_4K_EXPORT,
    VIDEO_EXPORT,
    get_plan,
)
from aura_music_studio.renderer_runtime import _smoke_status
from aura_music_studio.video_engines import public_video_engine_status, render_neural_scene
from aura_music_studio.video_studio import (
    build_storyboard,
    render_visualizer,
    validate_music_video,
    video_dimensions,
    write_lyric_srt,
)


def _tone(path: Path, seconds: float = 3.2, sr: int = 48000) -> Path:
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    wave = 0.15 * np.sin(2 * np.pi * 220.0 * t)
    sf.write(path, wave, sr, subtype="PCM_24")
    return path


def test_video_tier_progression():
    free = get_plan("free")
    base = get_plan("base")
    pro = get_plan("pro")
    assert free.has(BASIC_VIDEO_STUDIO)
    assert not free.has(VIDEO_EXPORT)
    assert base.has(VIDEO_EXPORT)
    assert base.has(LYRIC_VIDEO)
    assert not base.has(NEURAL_VIDEO)
    assert pro.has(NEURAL_VIDEO)
    assert pro.has(VIDEO_4K_EXPORT)


def test_renderer_model_defaults_to_deployment_profile(monkeypatch):
    monkeypatch.setenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-turbo")
    assert RendererConfig().model == "acestep-v15-turbo"
    monkeypatch.setenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-xl-turbo")
    assert RendererConfig().model == "acestep-v15-xl-turbo"


def test_video_dimensions_profiles():
    assert video_dimensions("16:9", "hd") == (1920, 1080)
    assert video_dimensions("9:16", "hd") == (1080, 1920)
    assert video_dimensions("1:1", "4k") == (2160, 2160)
    w, h = video_dimensions("9:16", "preview")
    assert h <= 720 and w % 2 == 0 and h % 2 == 0


def test_storyboard_and_srt_are_audio_driven(tmp_path: Path):
    audio = _tone(tmp_path / "tone.wav", seconds=4.0)
    board = build_storyboard(audio, direction="cosmic performance", scene_beats=4)
    assert board["duration_seconds"] >= 3.9
    assert board["scenes"]
    assert all(scene["end"] > scene["start"] for scene in board["scenes"])
    srt = write_lyric_srt("[Verse 1]\nOne line\nSecond line", tmp_path / "lyrics.srt", duration=4.0)
    text = srt.read_text(encoding="utf-8")
    assert "One line" in text and "Second line" in text and "-->" in text


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_local_visualizer_renders_real_music_video(tmp_path: Path):
    audio = _tone(tmp_path / "tone.wav", seconds=3.2)
    output, report = render_visualizer(
        audio,
        tmp_path / "preview.mp4",
        aspect="16:9",
        quality="preview",
        fps=24,
        duration_limit=3.0,
        waveform=True,
    )
    assert output.is_file() and output.stat().st_size > 4096
    probe = validate_music_video(output, minimum_seconds=2.5)
    assert probe["has_video"] and probe["has_audio"]
    assert report["mode"] == "visualizer"


def test_neural_video_status_never_exposes_commands(monkeypatch):
    monkeypatch.setenv("AURA_WAN_VIDEO_CMD", "/definitely/not/a/real/program --arg")
    rows = public_video_engine_status()
    wan = next(x for x in rows if x["id"] == "wan22")
    assert wan["configured"] is False
    assert wan["command_exposed"] is False
    assert "AURA_WAN_VIDEO_CMD" not in str(rows)


def test_unconfigured_neural_video_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AURA_WAN_VIDEO_CMD", raising=False)
    with pytest.raises(RuntimeError):
        render_neural_scene(
            "wan22",
            prompt="original cinematic scene",
            output=tmp_path / "scene.mp4",
            duration_seconds=3,
            width=640,
            height=360,
            fps=24,
        )


def test_renderer_smoke_status_is_safe_and_machine_readable(tmp_path: Path, monkeypatch):
    status = tmp_path / "renderer_smoke.json"
    status.write_text(
        '{"engine":"ACE-Step 1.5","model":"acestep-v15-turbo","real_audio_verified":true,"duration_seconds":10.0,"sample_rate":48000,"channels":2}',
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_RENDERER_SMOKE_STATUS", str(status))
    value = _smoke_status()
    assert value["real_audio_verified"] is True
    assert value["model"] == "acestep-v15-turbo"
    assert "path" not in value
