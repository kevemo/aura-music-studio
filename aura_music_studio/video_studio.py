from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import librosa
import numpy as np
from pydantic import BaseModel, Field

from .video_sync import audio_beats

Aspect = Literal["16:9", "9:16", "1:1", "4:5"]
VideoMode = Literal["visualizer", "lyric_video", "montage"]


class StoryboardScene(BaseModel):
    index: int
    start: float
    end: float
    energy: float = Field(ge=0.0, le=1.0)
    prompt: str
    beat_count: int = 0


class MusicVideoRequest(BaseModel):
    audio_asset_id: str
    visual_asset_ids: list[str] = Field(default_factory=list, max_length=100)
    mode: VideoMode = "visualizer"
    title: str = ""
    artist: str = ""
    lyrics: str = ""
    creative_direction: str = "cinematic, musical, emotionally aligned to the song"
    aspect: Aspect = "16:9"
    fps: int = Field(default=30, ge=24, le=60)
    preview_seconds: float | None = Field(default=None, ge=5.0, le=60.0)
    include_waveform: bool = True
    quality: Literal["preview", "hd", "4k"] = "hd"


ASPECT_SIZE = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def video_dimensions(aspect: Aspect, quality: str = "hd") -> tuple[int, int]:
    w, h = ASPECT_SIZE[aspect]
    if quality == "preview":
        scale = min(1.0, 720 / max(w, h))
        return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    if quality == "4k":
        return w * 2, h * 2
    return w, h


def _ffmpeg() -> str:
    value = shutil.which("ffmpeg")
    if not value:
        raise RuntimeError("FFmpeg is required for the local ESP Video Studio")
    return value


def _ffprobe() -> str:
    value = shutil.which("ffprobe")
    if not value:
        raise RuntimeError("ffprobe is required for the local ESP Video Studio")
    return value


def media_probe(path: str | Path) -> dict:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    proc = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=codec_type,width,height,sample_rate,channels", "-of", "json", str(source)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(proc.stdout)
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    streams = payload.get("streams") or []
    return {
        "duration_seconds": duration,
        "has_video": any(x.get("codec_type") == "video" for x in streams),
        "has_audio": any(x.get("codec_type") == "audio" for x in streams),
        "streams": streams,
    }


def validate_music_video(path: str | Path, *, minimum_seconds: float = 1.0) -> dict:
    report = media_probe(path)
    if not report["has_video"]:
        raise RuntimeError("Rendered music video has no video stream")
    if not report["has_audio"]:
        raise RuntimeError("Rendered music video has no audio stream")
    if report["duration_seconds"] < minimum_seconds:
        raise RuntimeError("Rendered music video is too short")
    return report


def build_storyboard(audio_path: str | Path, *, direction: str, scene_beats: int = 16) -> dict:
    """Create a deterministic beat-aligned storyboard usable by local or neural video renderers."""
    source = Path(audio_path).resolve()
    timing = audio_beats(source)
    y, sr = librosa.load(source, sr=22050, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    beats = [float(x) for x in timing["beat_times"] if 0 <= float(x) <= duration]
    if not beats:
        tempo = timing.get("tempo_bpm") or 120.0
        step = 60.0 / max(float(tempo), 1.0)
        beats = list(np.arange(0.0, duration, step, dtype=float))

    boundaries = [0.0]
    for index in range(scene_beats, len(beats), scene_beats):
        t = beats[index]
        if t - boundaries[-1] >= 2.0:
            boundaries.append(t)
    if duration - boundaries[-1] < 1.0 and len(boundaries) > 1:
        boundaries[-1] = duration
    elif boundaries[-1] < duration:
        boundaries.append(duration)

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512).reshape(-1)
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)
    peak = float(np.percentile(rms, 95)) if rms.size else 1.0
    scenes: list[StoryboardScene] = []
    for idx, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:]), 1):
        mask = (times >= start) & (times < end)
        value = float(np.mean(rms[mask])) if np.any(mask) else 0.0
        energy = min(1.0, max(0.0, value / max(peak, 1e-9)))
        count = sum(1 for beat in beats if start <= beat < end)
        scenes.append(StoryboardScene(
            index=idx,
            start=round(start, 3),
            end=round(end, 3),
            energy=round(energy, 4),
            beat_count=count,
            prompt=(
                f"{direction.strip() or 'cinematic music video'}. Scene {idx}; "
                f"music energy {energy:.2f}; visual movement and edit intensity should follow the music; "
                "consistent art direction, cinematic lighting, no text unless explicitly requested"
            ),
        ))
    return {
        "duration_seconds": duration,
        "tempo_bpm": timing["tempo_bpm"],
        "scene_beats": scene_beats,
        "scenes": [x.model_dump() for x in scenes],
    }


def _scale_crop(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def _background_clip(source: Path | None, target: Path, *, duration: float, width: int, height: int, fps: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is None:
        command = [
            _ffmpeg(), "-y", "-f", "lavfi", "-i",
            f"color=c=0x110018:s={width}x{height}:r={fps}:d={duration:.3f}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(target),
        ]
    elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        command = [
            _ffmpeg(), "-y", "-loop", "1", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", _scale_crop(width, height), "-r", str(fps), "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-preset", "veryfast", str(target),
        ]
    else:
        command = [
            _ffmpeg(), "-y", "-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", _scale_crop(width, height), "-r", str(fps), "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-preset", "veryfast", str(target),
        ]
    subprocess.run(command, check=True, timeout=max(120, int(duration * 8)))


def _srt_timestamp(value: float) -> str:
    ms = max(0, int(round(value * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_lyric_srt(lyrics: str, target: str | Path, *, duration: float) -> Path:
    """Create a simple readable lyric timing track when no LRC/SRT timestamps were supplied."""
    target = Path(target)
    lines = []
    for raw in lyrics.splitlines():
        text = raw.strip()
        if not text or re.fullmatch(r"\[[^\]]+\]", text):
            continue
        lines.append(text)
    if not lines:
        raise ValueError("Lyric video requires lyrics")
    slot = duration / len(lines)
    rows = []
    for idx, text in enumerate(lines, 1):
        start = idx * slot - slot
        end = min(duration, start + slot * 0.92)
        rows.append(f"{idx}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(rows), encoding="utf-8")
    return target


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _mux_reactive(background: Path, audio: Path, target: Path, *, width: int, height: int, fps: int, waveform: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [_ffmpeg(), "-y", "-i", str(background), "-i", str(audio)]
    if waveform:
        wave_height = max(80, int(height * 0.12))
        filters = (
            f"[1:a]showwaves=s={width}x{wave_height}:mode=cline:colors=0xE9C46A@0.90:rate={fps},"
            f"format=rgba[wave];[0:v][wave]overlay=0:H-h-{max(24, int(height*0.04))}:shortest=1[v]"
        )
        command += ["-filter_complex", filters, "-map", "[v]", "-map", "1:a:0"]
    else:
        command += ["-map", "0:v:0", "-map", "1:a:0"]
    command += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(target),
    ]
    subprocess.run(command, check=True, timeout=7200)


def render_visualizer(
    audio: str | Path,
    output: str | Path,
    *,
    visual: str | Path | None = None,
    aspect: Aspect = "16:9",
    fps: int = 30,
    quality: str = "hd",
    duration_limit: float | None = None,
    waveform: bool = True,
) -> tuple[Path, dict]:
    audio = Path(audio).resolve()
    visual_path = Path(visual).resolve() if visual else None
    info = media_probe(audio)
    duration = info["duration_seconds"]
    if duration_limit is not None:
        duration = min(duration, float(duration_limit))
    width, height = video_dimensions(aspect, quality)
    output = Path(output).resolve()
    work = output.parent / f".{output.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    background = work / "background.mp4"
    _background_clip(visual_path, background, duration=duration, width=width, height=height, fps=fps)
    _mux_reactive(background, audio, output, width=width, height=height, fps=fps, waveform=waveform)
    shutil.rmtree(work, ignore_errors=True)
    report = validate_music_video(output, minimum_seconds=min(3.0, duration))
    report.update({"mode": "visualizer", "aspect": aspect, "width": width, "height": height, "fps": fps})
    return output, report


def render_lyric_video(
    audio: str | Path,
    output: str | Path,
    *,
    lyrics: str,
    visual: str | Path | None = None,
    aspect: Aspect = "16:9",
    fps: int = 30,
    quality: str = "hd",
    duration_limit: float | None = None,
    waveform: bool = True,
) -> tuple[Path, dict]:
    output = Path(output).resolve()
    base = output.with_name(output.stem + "_base.mp4")
    base, base_report = render_visualizer(
        audio, base, visual=visual, aspect=aspect, fps=fps, quality=quality,
        duration_limit=duration_limit, waveform=waveform,
    )
    duration = base_report["duration_seconds"]
    srt = write_lyric_srt(lyrics, output.with_suffix(".srt"), duration=duration)
    style = "FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00100020,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=80"
    vf = f"subtitles='{_filter_path(srt)}':force_style='{style}'"
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(base), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(output)],
        check=True,
        timeout=7200,
    )
    base.unlink(missing_ok=True)
    report = validate_music_video(output, minimum_seconds=min(3.0, duration))
    report.update({"mode": "lyric_video", "subtitle_file": srt.name, "aspect": aspect})
    return output, report


def render_montage(
    audio: str | Path,
    visuals: list[str | Path],
    output: str | Path,
    *,
    aspect: Aspect = "16:9",
    fps: int = 30,
    quality: str = "hd",
    duration_limit: float | None = None,
    waveform: bool = False,
) -> tuple[Path, dict]:
    if not visuals:
        raise ValueError("Montage mode needs at least one uploaded image/video")
    audio = Path(audio).resolve()
    output = Path(output).resolve()
    total = media_probe(audio)["duration_seconds"]
    if duration_limit is not None:
        total = min(total, float(duration_limit))
    width, height = video_dimensions(aspect, quality)
    storyboard = build_storyboard(audio, direction="beat-synchronised visual montage")
    scenes = [x for x in storyboard["scenes"] if x["start"] < total]
    if not scenes:
        scenes = [{"start": 0.0, "end": total}]
    work = output.parent / f".{output.stem}_montage"
    work.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    resolved = [Path(x).resolve() for x in visuals]
    for idx, scene in enumerate(scenes):
        start = float(scene["start"])
        end = min(total, float(scene["end"]))
        length = max(0.25, end - start)
        clip = work / f"scene_{idx:04d}.mp4"
        _background_clip(resolved[idx % len(resolved)], clip, duration=length, width=width, height=height, fps=fps)
        clips.append(clip)
    concat = work / "concat.txt"
    concat.write_text("\n".join(f"file '{str(p).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for p in clips), encoding="utf-8")
    picture = work / "picture.mp4"
    subprocess.run(
        [_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(picture)],
        check=True,
        timeout=7200,
    )
    _mux_reactive(picture, audio, output, width=width, height=height, fps=fps, waveform=waveform)
    shutil.rmtree(work, ignore_errors=True)
    report = validate_music_video(output, minimum_seconds=min(3.0, total))
    report.update({"mode": "montage", "scene_count": len(clips), "aspect": aspect, "storyboard": storyboard})
    return output, report
