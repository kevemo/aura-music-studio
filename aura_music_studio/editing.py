from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import soundfile as sf
from pydantic import BaseModel, Field

from .acestep_api import AceStepClient
from .session import Clip, StudioSession


class RegionEditRequest(BaseModel):
    operation: str = Field(pattern="^(replace|repaint|extend|variation)$")
    start_seconds: float = 0.0
    end_seconds: float | None = None
    prompt: str = ""
    negative_prompt: str = ""
    strength: float = Field(default=0.65, ge=0.0, le=1.0)
    target_duration_seconds: float | None = None


def duration(path: Path) -> float:
    info = sf.info(path)
    return float(info.frames / info.samplerate)


def _run_external_region_generator(source: Path, output: Path, request: RegionEditRequest) -> Path:
    command = os.getenv("AURA_REGION_RENDER_CMD")
    if not command:
        raise RuntimeError("No external region renderer is configured")
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "AURA_SOURCE": str(source),
        "AURA_OUTPUT": str(output),
        "AURA_EDIT_OPERATION": request.operation,
        "AURA_EDIT_START": str(request.start_seconds),
        "AURA_EDIT_END": "" if request.end_seconds is None else str(request.end_seconds),
        "AURA_EDIT_PROMPT": request.prompt,
        "AURA_EDIT_NEGATIVE_PROMPT": request.negative_prompt,
        "AURA_EDIT_STRENGTH": str(request.strength),
        "AURA_EDIT_TARGET_DURATION": "" if request.target_duration_seconds is None else str(request.target_duration_seconds),
        "AURA_REAL_AUDIO_REQUIRED": "1",
    })
    subprocess.run(shlex.split(command), env=env, check=True)
    if not output.exists():
        raise RuntimeError(f"Region renderer did not create {output}")
    return output


def replace_region(source: Path, generated_region: Path, output: Path, start: float, end: float, crossfade_ms: int = 35) -> Path:
    """Replace a time range with a generated region when the renderer returns region-only audio."""
    if end <= start:
        raise ValueError("end must be greater than start")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    target_len = end - start
    fade = min(crossfade_ms / 1000.0, target_len / 4.0)
    filter_complex = (
        f"[0:a]atrim=0:{start},asetpts=PTS-STARTPTS[p];"
        f"[1:a]atrim=0:{target_len},apad=pad_dur={target_len},atrim=0:{target_len},"
        f"afade=t=in:st=0:d={fade},afade=t=out:st={max(0.0,target_len-fade)}:d={fade}[r];"
        f"[0:a]atrim=start={end},asetpts=PTS-STARTPTS[s];"
        "[p][r][s]concat=n=3:v=0:a=1[out]"
    )
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-i", str(generated_region),
        "-filter_complex", filter_complex, "-map", "[out]",
        "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ], check=True)
    return output


def generate_region_take(source: Path, request: RegionEditRequest, work_dir: Path, take_number: int) -> Path:
    """Generate an alternate real-audio take while preserving the original as another lane."""
    work_dir.mkdir(parents=True, exist_ok=True)

    if request.operation in {"replace", "repaint", "variation"}:
        if request.end_seconds is None:
            raise ValueError("replace/repaint/variation requires end_seconds")
        # Preferred path: ACE-Step's native repaint preserves non-selected context itself.
        if os.getenv("AURA_ACESTEP_API_URL"):
            client = AceStepClient()
            rendered = client.repaint(
                source,
                work_dir / f"ace_repaint_{take_number:02d}",
                prompt=request.prompt,
                start=request.start_seconds,
                end=request.end_seconds,
                strength=request.strength,
                model=os.getenv("AURA_ACESTEP_EDIT_MODEL") or None,
            )
            return rendered

        # Generic external region engines may return only the replacement slice; splice it safely.
        generated = work_dir / f"generated_region_{take_number:02d}.wav"
        _run_external_region_generator(source, generated, request)
        committed = work_dir / f"committed_take_{take_number:02d}.wav"
        return replace_region(source, generated, committed, request.start_seconds, request.end_seconds)

    # Extension requires a renderer capable of returning the extended complete waveform.
    generated = work_dir / f"extended_take_{take_number:02d}.wav"
    return _run_external_region_generator(source, generated, request)


def add_take_to_session(
    session: StudioSession,
    *,
    track_id: str,
    audio_path: Path,
    name: str,
    start: float = 0.0,
    duration_seconds: float | None = None,
    generation_metadata: dict | None = None,
) -> Clip:
    track = session.find_track(track_id)
    lanes = [c.take_lane for c in track.clips]
    lane = max(lanes, default=-1) + 1
    clip = Clip(
        name=name,
        kind="audio",
        source=str(audio_path),
        start=start,
        duration=duration_seconds if duration_seconds is not None else duration(audio_path),
        take_lane=lane,
        metadata={"real_audio": True, **(generation_metadata or {})},
    )
    track.clips.append(clip)
    session.generation_history.append({
        "track_id": track_id,
        "clip_id": clip.id,
        "take_lane": lane,
        "source": str(audio_path),
        **(generation_metadata or {}),
    })
    session.touch()
    return clip
