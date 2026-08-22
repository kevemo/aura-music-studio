from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def evaluate_audio(path: Path, target_duration: float | None = None, target_bpm: float | None = None) -> dict:
    y, sr = librosa.load(path, sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    silence_ratio = float(np.mean(np.abs(y) < 1e-4)) if y.size else 1.0
    clipping_ratio = float(np.mean(np.abs(y) >= 0.995)) if y.size else 0.0
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    measured_bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else None

    scores: list[float] = []
    duration_score = 1.0
    if target_duration and target_duration > 0:
        duration_score = max(0.0, 1.0 - abs(duration - target_duration) / max(target_duration, 1.0))
    scores.append(duration_score)

    silence_score = max(0.0, 1.0 - max(0.0, silence_ratio - 0.08) / 0.75)
    scores.append(silence_score)

    clipping_score = max(0.0, 1.0 - clipping_ratio * 50.0)
    scores.append(clipping_score)

    level_score = min(1.0, max(0.0, rms / 0.08))
    scores.append(level_score)

    tempo_score = 1.0
    if target_bpm and measured_bpm:
        # Beat trackers commonly return half/double tempo; score the closest octave-equivalent.
        candidates = [measured_bpm, measured_bpm * 2.0, measured_bpm / 2.0]
        error = min(abs(x - target_bpm) for x in candidates)
        tempo_score = max(0.0, 1.0 - error / max(target_bpm * 0.18, 1.0))
    scores.append(tempo_score)

    quality_score = float(np.mean(scores))
    return {
        "quality_score": quality_score,
        "duration_seconds": duration,
        "peak": peak,
        "rms": rms,
        "silence_ratio": silence_ratio,
        "clipping_ratio": clipping_ratio,
        "measured_bpm": measured_bpm,
        "duration_score": duration_score,
        "tempo_score": tempo_score,
        "passes_basic_integrity": bool(
            duration > 5.0 and rms > 0.003 and silence_ratio < 0.90 and clipping_ratio < 0.03
        ),
    }
