from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Literal

import librosa
import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field
from scipy.ndimage import median_filter


NOTE_TO_PC = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
              "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "chromatic": list(range(12)),
}


class AutoTuneSettings(BaseModel):
    mode: Literal["natural", "classic", "hard", "robot", "custom"] = "natural"
    key: str | None = None
    scale: str = "major"
    custom_pitch_classes: list[int] = Field(default_factory=list)
    strength: float = Field(default=.75, ge=0.0, le=1.0)
    retune_speed_ms: float = Field(default=80.0, ge=0.0, le=500.0)
    humanize: float = Field(default=.65, ge=0.0, le=1.0)
    formant_preserve: bool = True
    min_note_ms: float = Field(default=90.0, ge=20.0, le=1000.0)


def detect_key(source: Path) -> dict:
    y, sr = librosa.load(source, sr=None, mono=True, duration=180)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    scores = []
    for root in range(12):
        scores.append((float(np.dot(profile, np.roll(major, root))), root, "major"))
        scores.append((float(np.dot(profile, np.roll(minor, root))), root, "minor"))
    score, root, scale = max(scores)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return {"key": names[root], "scale": scale, "confidence_score": score}


def _allowed_pcs(settings: AutoTuneSettings, detected: dict) -> tuple[int, list[int], str]:
    key_name = (settings.key or detected["key"]).strip().upper().replace("♭", "B").replace("♯", "#")
    root = NOTE_TO_PC.get(key_name)
    if root is None:
        raise ValueError(f"Unsupported key: {key_name}")
    if settings.mode == "custom" and settings.custom_pitch_classes:
        pcs = sorted({int(x) % 12 for x in settings.custom_pitch_classes})
        return root, pcs, "custom"
    scale_name = settings.scale.lower().strip()
    intervals = SCALES.get(scale_name)
    if intervals is None:
        raise ValueError(f"Unsupported scale: {settings.scale}")
    return root, [(root + x) % 12 for x in intervals], scale_name


def _nearest_allowed(midi_value: float, pcs: list[int]) -> float:
    base = int(round(midi_value))
    candidates = [n for n in range(base - 12, base + 13) if n % 12 in pcs]
    return float(min(candidates, key=lambda n: abs(n - midi_value)))


def analyze_pitch(source: Path, settings: AutoTuneSettings) -> dict:
    y, sr = librosa.load(source, sr=None, mono=True)
    hop = 512
    f0, voiced, voiced_prob = librosa.pyin(
        y, sr=sr, hop_length=hop,
        fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"),
    )
    detected = detect_key(source)
    _, pcs, scale_name = _allowed_pcs(settings, detected)
    midi = librosa.hz_to_midi(f0)
    target = np.array([_nearest_allowed(v, pcs) if np.isfinite(v) else np.nan for v in midi], dtype=float)
    cents = (target - midi) * 100.0
    valid = cents[np.isfinite(cents)]
    return {
        "detected": detected,
        "selected_key": settings.key or detected["key"],
        "selected_scale": scale_name,
        "voiced_ratio": float(np.mean(voiced)) if voiced is not None else 0.0,
        "median_absolute_correction_cents": float(np.median(np.abs(valid))) if valid.size else 0.0,
        "p90_absolute_correction_cents": float(np.percentile(np.abs(valid), 90)) if valid.size else 0.0,
        "frames": int(len(f0)),
        "sample_rate": int(sr),
    }


def _external_render(source: Path, output: Path, settings: AutoTuneSettings, analysis: dict) -> Path | None:
    command = (os.getenv("AURA_AUTOTUNE_CMD") or "").strip()
    if not command:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "AURA_TUNE_INPUT": str(source),
        "AURA_TUNE_OUTPUT": str(output),
        "AURA_TUNE_SETTINGS": settings.model_dump_json(),
        "AURA_TUNE_ANALYSIS": json.dumps(analysis),
        "AURA_TUNE_KEY": str(analysis["selected_key"]),
        "AURA_TUNE_SCALE": str(analysis["selected_scale"]),
        "AURA_TUNE_STRENGTH": str(settings.strength),
        "AURA_TUNE_RETUNE_MS": str(settings.retune_speed_ms),
        "AURA_TUNE_HUMANIZE": str(settings.humanize),
        "AURA_TUNE_FORMANT_PRESERVE": "1" if settings.formant_preserve else "0",
    })
    subprocess.run(shlex.split(command), env=env, check=True)
    if not output.exists():
        raise RuntimeError("Configured Aura Tune backend did not create output")
    return output


def _builtin_render(source: Path, output: Path, settings: AutoTuneSettings, analysis: dict) -> Path:
    """Offline note-region pitch correction fallback for isolated vocals.

    This is intentionally conservative. A configured dedicated tuning backend remains preferred for
    transparent formant-aware real-time correction, while this fallback guarantees useful offline
    correction without a commercial plugin dependency.
    """
    audio, sr = sf.read(source, always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    hop = 512
    f0, voiced, _ = librosa.pyin(
        mono, sr=sr, hop_length=hop,
        fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"),
    )
    detected = analysis["detected"]
    _, pcs, _ = _allowed_pcs(settings, detected)
    midi = librosa.hz_to_midi(f0)
    target = np.array([_nearest_allowed(v, pcs) if np.isfinite(v) else np.nan for v in midi], dtype=float)
    delta = np.where(np.isfinite(midi), (target - midi) * settings.strength, 0.0)

    if settings.mode == "natural":
        delta *= .65 + .35 * (1.0 - settings.humanize)
        delta = median_filter(delta, size=9, mode="nearest")
    elif settings.mode == "classic":
        delta = median_filter(delta, size=5, mode="nearest")
    elif settings.mode == "hard":
        delta *= 1.0
    elif settings.mode == "robot":
        delta *= 1.0
        settings = settings.model_copy(update={"humanize": 0.0})

    min_frames = max(1, int((settings.min_note_ms / 1000.0) * sr / hop))
    corrected = audio.copy()
    n_frames = len(delta)
    i = 0
    while i < n_frames:
        if voiced is None or not bool(voiced[i]) or abs(delta[i]) < .05:
            i += 1
            continue
        j = i + 1
        while j < n_frames and bool(voiced[j]) and abs(delta[j] - delta[i]) < .45:
            j += 1
        if j - i < min_frames:
            i = j
            continue
        start = max(0, i * hop - int(.02 * sr))
        end = min(len(audio), j * hop + int(.02 * sr))
        shift = float(np.median(delta[i:j]))
        if abs(shift) > .03:
            region = audio[start:end]
            for ch in range(region.shape[1]):
                shifted = librosa.effects.pitch_shift(region[:, ch], sr=sr, n_steps=shift)
                corrected[start:end, ch] = shifted[: end - start]
        i = j

    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, corrected, sr, subtype="PCM_24")
    return output


def tune_vocal(source: Path, output: Path, settings: AutoTuneSettings) -> tuple[Path, dict]:
    analysis = analyze_pitch(source, settings)
    rendered = _external_render(source, output, settings, analysis)
    backend = "external_formant_aware" if rendered else "aura_offline_note_region"
    if rendered is None:
        rendered = _builtin_render(source, output, settings, analysis)
    report = {
        **analysis,
        "mode": settings.mode,
        "strength": settings.strength,
        "retune_speed_ms": settings.retune_speed_ms,
        "humanize": settings.humanize,
        "formant_preserve_requested": settings.formant_preserve,
        "backend": backend,
        "note": (
            "Dedicated AURA_AUTOTUNE_CMD backend is preferred for maximum transparency and formant-aware real-time tuning."
            if backend != "external_formant_aware" else "Professional external/local tuning backend used."
        ),
    }
    return rendered, report
