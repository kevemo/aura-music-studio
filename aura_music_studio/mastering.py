from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf


@dataclass(frozen=True)
class MasterPreset:
    target_lufs: float
    true_peak_db: float
    low_shelf_db: float = 0.0
    high_shelf_db: float = 0.0
    compression: str = "2:1"
    notes: str = ""


PRESETS = {
    "streaming": MasterPreset(-14.0, -1.0, notes="Balanced general streaming master"),
    "pop": MasterPreset(-10.5, -0.8, 0.2, 0.8, "2.5:1", "Forward modern pop"),
    "rock": MasterPreset(-10.0, -0.8, 0.5, 0.5, "2.5:1", "Punchy live-band rock"),
    "acoustic": MasterPreset(-14.0, -1.0, -0.2, 0.5, "1.8:1", "Open acoustic dynamics"),
    "ballad": MasterPreset(-13.0, -1.0, 0.2, 0.5, "1.8:1", "Wide emotional ballad"),
    "electronic": MasterPreset(-9.0, -0.7, 0.4, 0.8, "3:1", "Dense electronic production"),
    "hiphop": MasterPreset(-9.5, -0.8, 0.8, 0.4, "3:1", "Low-end-forward hip-hop"),
    "cinematic": MasterPreset(-16.0, -1.0, 0.2, 0.6, "1.5:1", "High dynamic range"),
    "karaoke": MasterPreset(-13.0, -1.0, 0.0, 0.4, "2:1", "Lead-vocal-friendly backing master"),
}


def analyze_master(path: Path) -> dict:
    audio, sr = sf.read(path, always_2d=True, dtype="float32")
    if len(audio) == 0:
        raise ValueError("Empty audio file")
    meter = pyln.Meter(sr)
    mono = audio.mean(axis=1)
    try:
        lufs = float(meter.integrated_loudness(mono))
    except Exception:
        lufs = None
    peak = float(np.max(np.abs(audio)))
    true_peak_db = 20.0 * math.log10(max(peak, 1e-12))
    y = mono
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    rms = librosa.feature.rms(y=y)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    return {
        "sample_rate": sr,
        "channels": int(audio.shape[1]),
        "duration_seconds": len(audio) / sr,
        "integrated_lufs": lufs,
        "sample_peak_dbfs": true_peak_db,
        "rms": float(np.mean(rms)),
        "spectral_centroid_hz": float(np.mean(centroid)),
        "spectral_bandwidth_hz": float(np.mean(bandwidth)),
        "zero_crossing_rate": float(np.mean(zcr)),
    }


def master(
    source: Path,
    output: Path,
    *,
    preset: str = "streaming",
    target_lufs: float | None = None,
    true_peak_db: float | None = None,
    reference: Path | None = None,
) -> tuple[Path, dict]:
    """Master a track, preferring reference matching when Matchering is installed.

    Reference mastering is optional and deliberately isolated so the core remains usable without it.
    """
    if reference and reference.exists():
        try:
            import matchering as mg
            output.parent.mkdir(parents=True, exist_ok=True)
            mg.process(
                target=str(source),
                reference=str(reference),
                results=[mg.pcm24(str(output))],
            )
            report = {
                "method": "matchering_reference",
                "reference": str(reference),
                "analysis": analyze_master(output),
            }
            return output, report
        except Exception as exc:
            fallback_reason = f"Reference mastering unavailable/failed: {type(exc).__name__}: {exc}"
    else:
        fallback_reason = None

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for Aura mastering fallback")
    p = PRESETS.get(preset, PRESETS["streaming"])
    target_lufs = p.target_lufs if target_lufs is None else target_lufs
    true_peak_db = p.true_peak_db if true_peak_db is None else true_peak_db
    output.parent.mkdir(parents=True, exist_ok=True)

    # A deliberately conservative chain. Generative models provide the creative mix;
    # Aura adds cleanup, gentle tonal shaping, loudness consistency and peak safety.
    filters = ["highpass=f=24", "lowpass=f=19500"]
    if abs(p.low_shelf_db) >= 0.05:
        filters.append(f"bass=g={p.low_shelf_db}:f=120:w=0.7")
    if abs(p.high_shelf_db) >= 0.05:
        filters.append(f"treble=g={p.high_shelf_db}:f=8500:w=0.6")
    filters += [
        "acompressor=threshold=-18dB:ratio=2.0:attack=18:release=180:makeup=1.2dB",
        f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11",
        "alimiter=limit=0.95:attack=5:release=50",
    ]
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", ",".join(filters), "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ], check=True)
    report = {
        "method": "adaptive_ffmpeg",
        "preset": preset,
        "target_lufs": target_lufs,
        "true_peak_db": true_peak_db,
        "fallback_reason": fallback_reason,
        "analysis": analyze_master(output),
    }
    return output, report


def translation_report(path: Path) -> dict:
    """Measure likely translation risks and optionally create phone/mono preview metrics."""
    audio, sr = sf.read(path, always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    side = (audio[:, 0] - audio[:, 1]) / 2.0 if audio.shape[1] > 1 else np.zeros_like(mono)
    mid = (audio[:, 0] + audio[:, 1]) / 2.0 if audio.shape[1] > 1 else mono
    side_ratio = float(np.sqrt(np.mean(side**2)) / max(np.sqrt(np.mean(mid**2)), 1e-8))

    stft = np.abs(librosa.stft(mono, n_fft=4096, hop_length=1024)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    total = float(stft.sum()) or 1.0
    low = float(stft[freqs < 90].sum() / total)
    sub = float(stft[freqs < 45].sum() / total)
    presence = float(stft[(freqs >= 1500) & (freqs <= 5000)].sum() / total)
    high = float(stft[freqs > 12000].sum() / total)

    warnings = []
    if side_ratio > 0.75:
        warnings.append("Very wide stereo image may lose elements in mono playback.")
    if sub > 0.16:
        warnings.append("High sub-bass proportion may translate poorly to phone speakers.")
    if presence < 0.08:
        warnings.append("Low 1.5–5 kHz presence may reduce clarity on small speakers.")
    if high > 0.20:
        warnings.append("Large ultrasonic/high-frequency proportion may sound brittle after lossy encoding.")
    return {
        "stereo_side_to_mid_rms": side_ratio,
        "energy_below_45hz": sub,
        "energy_below_90hz": low,
        "presence_1_5_to_5khz": presence,
        "energy_above_12khz": high,
        "warnings": warnings,
    }


def save_master_report(path: Path, report: dict, translation: dict, destination: Path) -> Path:
    destination.write_text(json.dumps({"master": report, "translation": translation}, indent=2), encoding="utf-8")
    return destination
