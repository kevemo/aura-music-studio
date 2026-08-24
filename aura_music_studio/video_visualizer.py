from __future__ import annotations

import os
import subprocess
from pathlib import Path


class VideoVisualizerError(RuntimeError):
    pass


def render_audio_visualizer(
    *,
    audio_path: str | Path,
    output_path: str | Path,
    cover_image: str | Path | None = None,
    aspect_ratio: str = "9:16",
    title: str = "",
) -> Path:
    """Render a real MP4 visualizer using FFmpeg.

    This is a deterministic studio tool rather than generative AI. It lets any
    completed Live Sound Studio song become a social-ready video even when no
    paid video-generation provider is configured.
    """
    audio = Path(audio_path)
    output = Path(output_path)
    if not audio.exists():
        raise VideoVisualizerError(f"Audio file not found: {audio}")
    output.parent.mkdir(parents=True, exist_ok=True)

    size = {"9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080"}.get(aspect_ratio)
    if not size:
        raise VideoVisualizerError(f"Unsupported aspect ratio: {aspect_ratio}")
    width, height = size.split("x")

    if cover_image:
        cover = Path(cover_image)
        if not cover.exists():
            raise VideoVisualizerError(f"Cover image not found: {cover}")
        video_input = ["-loop", "1", "-i", str(cover)]
        filter_complex = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            "boxblur=18:2[bg];"
            f"[0:v]scale={int(int(width)*0.82)}:-2[cover];"
            "[bg][cover]overlay=(W-w)/2:(H-h)/2[base];"
            "[1:a]showwaves=s=900x240:mode=cline:rate=30,format=rgba,colorchannelmixer=aa=0.70[w];"
            "[base][w]overlay=(W-w)/2:H-h-120[v]"
        )
        cmd = [
            "ffmpeg", "-y", *video_input, "-i", str(audio),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "1:a:0", "-shortest",
            "-c:v", "libx264", "-preset", os.getenv("AURA_VIDEO_FFMPEG_PRESET", "medium"),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", str(output),
        ]
    else:
        filter_complex = (
            f"color=c=0x09050f:s={width}x{height}:r=30[bg];"
            "[0:a]showwaves=s=900x420:mode=cline:rate=30,format=rgba[w];"
            "[bg][w]overlay=(W-w)/2:(H-h)/2[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(audio),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a:0", "-shortest",
            "-c:v", "libx264", "-preset", os.getenv("AURA_VIDEO_FFMPEG_PRESET", "medium"),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", str(output),
        ]

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise VideoVisualizerError(completed.stderr[-2000:] or "FFmpeg visualizer render failed")
    if not output.exists() or output.stat().st_size < 4096:
        raise VideoVisualizerError("Visualizer renderer did not produce a valid MP4")
    return output
