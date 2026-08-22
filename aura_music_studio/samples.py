from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field


class SampleRequest(BaseModel):
    kind: str = Field(pattern="^(loop|one_shot|texture|fill|riff|transition)$")
    prompt: str
    duration_seconds: float = Field(default=4.0, ge=0.1, le=60.0)
    bpm: float | None = None
    key: str | None = None
    instrument: str | None = None
    bars: int | None = Field(default=None, ge=1, le=32)
    seed: int | None = None


class SampleAnalysis(BaseModel):
    duration_seconds: float
    sample_rate: int
    channels: int
    estimated_bpm: float | None = None
    key_hint: str | None = None
    peak_dbfs: float | None = None
    rms: float | None = None
    loop_boundary_score: float | None = None


def analyze_sample(path: Path) -> SampleAnalysis:
    info = sf.info(path)
    audio, sr = sf.read(path, always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    tempo = None
    key_hint = None
    boundary = None
    try:
        t, _ = librosa.beat.beat_track(y=mono, sr=sr)
        tempo = float(t[0] if hasattr(t, "__len__") else t)
    except Exception:
        pass
    try:
        chroma = librosa.feature.chroma_cqt(y=mono, sr=sr)
        pc = int(chroma.mean(axis=1).argmax())
        key_hint = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][pc]
    except Exception:
        pass
    try:
        window = min(len(mono) // 8, int(sr * .15))
        if window > 32:
            a = mono[:window]
            b = mono[-window:]
            # 1 = similar boundaries, useful as a rough seamless-loop indicator.
            denom = max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-9)
            boundary = float(np.clip((np.dot(a, b) / denom + 1.0) / 2.0, 0.0, 1.0))
    except Exception:
        pass
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    return SampleAnalysis(
        duration_seconds=float(info.frames / info.samplerate),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        estimated_bpm=tempo,
        key_hint=key_hint,
        peak_dbfs=float(20 * np.log10(max(peak, 1e-12))),
        rms=float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0,
        loop_boundary_score=boundary,
    )


def slice_sample(source: Path, output: Path, start_seconds: float, end_seconds: float, fade_ms: int = 5) -> Path:
    if end_seconds <= start_seconds:
        raise ValueError("end_seconds must be greater than start_seconds")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = end_seconds - start_seconds
    fade = min(fade_ms / 1000.0, duration / 5.0)
    filters = [f"atrim=start={start_seconds}:end={end_seconds}", "asetpts=PTS-STARTPTS"]
    if fade > 0:
        filters += [f"afade=t=in:st=0:d={fade}", f"afade=t=out:st={max(0.0,duration-fade)}:d={fade}"]
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", ",".join(filters), "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ], check=True)
    return output


def make_loop(source: Path, output: Path, *, bars: int, bpm: float, crossfade_ms: int = 12) -> Path:
    """Time-fit a real waveform to an exact musical loop length without creating MIDI audio."""
    if bars < 1 or bpm <= 0:
        raise ValueError("bars and bpm must be positive")
    target = bars * 4 * 60.0 / bpm
    current = sf.info(source).frames / sf.info(source).samplerate
    ratio = current / target
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    # atempo supports 0.5-2 per stage; factor into safe stages.
    factors = []
    remaining = ratio
    while remaining > 2:
        factors.append(2.0); remaining /= 2.0
    while remaining < .5:
        factors.append(.5); remaining /= .5
    factors.append(remaining)
    filters = [f"atempo={x:.8f}" for x in factors]
    fade = min(crossfade_ms / 1000.0, target / 10.0)
    filters += [f"atrim=0:{target}", "asetpts=PTS-STARTPTS", f"afade=t=in:st=0:d={fade}", f"afade=t=out:st={max(0.0,target-fade)}:d={fade}"]
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", ",".join(filters), "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ], check=True)
    return output


def generate_sample(request: SampleRequest, output: Path) -> Path:
    """Generate a REAL-AUDIO sample through a configured neural sample/layer engine."""
    command = os.getenv("AURA_SAMPLE_RENDER_CMD") or os.getenv("AURA_LAYER_RENDER_CMD")
    if not command:
        raise RuntimeError("Configure AURA_SAMPLE_RENDER_CMD or AURA_LAYER_RENDER_CMD with a neural real-audio generator")
    duration = request.duration_seconds
    if request.bars and request.bpm:
        duration = request.bars * 4 * 60.0 / request.bpm
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "AURA_OUTPUT": str(output),
        "AURA_SAMPLE_KIND": request.kind,
        "AURA_PROMPT": request.prompt,
        "AURA_DURATION": str(duration),
        "AURA_BPM": "" if request.bpm is None else str(request.bpm),
        "AURA_KEY": request.key or "",
        "AURA_INSTRUMENT": request.instrument or "",
        "AURA_BARS": "" if request.bars is None else str(request.bars),
        "AURA_SEED": "" if request.seed is None else str(request.seed),
        "AURA_REAL_AUDIO_REQUIRED": "1",
    })
    subprocess.run(shlex.split(command), env=env, check=True)
    if not output.exists():
        raise RuntimeError(f"Sample renderer did not create {output}")
    if output.suffix.lower() in {".mid", ".midi"}:
        output.unlink(missing_ok=True)
        raise RuntimeError("Aura refused a MIDI sample as generated audio")
    return output
