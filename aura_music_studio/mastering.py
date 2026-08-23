from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
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
    mid_db: float = 0.0
    high_shelf_db: float = 0.0
    compression_ratio: float = 2.0
    stereo_width: float = 1.0
    notes: str = ""


PRESETS = {
    "universal": MasterPreset(-13.0, -1.0, 0.0, 0.0, .4, 2.0, 1.0, "Balanced all-purpose master"),
    "streaming": MasterPreset(-14.0, -1.0, notes="Conservative streaming master"),
    "punch": MasterPreset(-10.5, -.8, .6, .2, .5, 2.8, 1.02, "Punchy transient-forward master"),
    "clarity": MasterPreset(-12.5, -1.0, -.2, .3, 1.2, 1.8, 1.02, "Open high-end clarity"),
    "warm": MasterPreset(-12.5, -1.0, .7, .1, -.3, 2.0, .98, "Warm rounded tonal balance"),
    "natural": MasterPreset(-14.0, -1.0, 0.0, 0.0, .2, 1.6, 1.0, "Preserve natural dynamics and timbre"),
    "spatial": MasterPreset(-13.5, -1.0, -.1, .0, .5, 1.8, 1.18, "Wide immersive presentation"),
    "cinematic": MasterPreset(-16.0, -1.0, .2, .0, .6, 1.5, 1.15, "High dynamic-range cinematic master"),
    "tape": MasterPreset(-13.0, -1.0, .5, .1, -.4, 1.9, .98, "Warm vintage/tape-oriented balance"),
    "pop": MasterPreset(-10.5, -.8, .2, .4, .8, 2.5, 1.05, "Forward modern pop"),
    "rock": MasterPreset(-10.0, -.8, .5, .5, .5, 2.5, 1.03, "Punchy live-band rock"),
    "acoustic": MasterPreset(-14.0, -1.0, -.2, .2, .5, 1.8, 1.02, "Open acoustic dynamics"),
    "ballad": MasterPreset(-13.0, -1.0, .2, .3, .5, 1.8, 1.08, "Wide emotional ballad"),
    "electronic": MasterPreset(-9.0, -.7, .4, .1, .8, 3.0, 1.08, "Dense electronic production"),
    "edm": MasterPreset(-8.5, -.6, .6, .2, .9, 3.2, 1.08, "High-energy club-oriented EDM"),
    "hiphop": MasterPreset(-9.5, -.8, .8, .4, .4, 3.0, 1.03, "Low-end-forward hip-hop"),
    "rnb": MasterPreset(-11.0, -.9, .4, .2, .5, 2.3, 1.06, "Smooth vocal-forward R&B"),
    "metal": MasterPreset(-9.0, -.7, .5, .5, .4, 3.0, 1.02, "Dense aggressive metal master"),
    "jazz": MasterPreset(-15.0, -1.0, .2, .2, .3, 1.5, 1.05, "Dynamic natural jazz"),
    "lofi": MasterPreset(-13.5, -1.0, .5, -.2, -.8, 1.8, .95, "Soft darker lo-fi balance"),
    "karaoke": MasterPreset(-13.0, -1.0, 0.0, -.2, .4, 2.0, 1.04, "Lead-vocal-friendly backing master"),
    "broadcast": MasterPreset(-16.0, -1.0, -.2, 1.0, .4, 2.8, 1.0, "Speech/broadcast-compatible master"),
}


def public_mastering_presets() -> list[dict]:
    return [{"id": name, **asdict(preset)} for name, preset in PRESETS.items()]


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
    centroid = librosa.feature.spectral_centroid(y=mono, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=mono, sr=sr)[0]
    rms = librosa.feature.rms(y=mono)[0]
    zcr = librosa.feature.zero_crossing_rate(mono)[0]
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
    intensity: float = 1.0,
    low_db: float = 0.0,
    mid_db: float = 0.0,
    high_db: float = 0.0,
    stereo_width: float | None = None,
) -> tuple[Path, dict]:
    """Master a track with one-click character presets plus optional expert controls."""
    intensity = max(0.0, min(float(intensity), 1.5))
    if reference and reference.exists():
        try:
            import matchering as mg
            output.parent.mkdir(parents=True, exist_ok=True)
            mg.process(target=str(source), reference=str(reference), results=[mg.pcm24(str(output))])
            report = {
                "method": "matchering_reference",
                "reference": str(reference),
                "intensity": intensity,
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
    width = p.stereo_width if stereo_width is None else max(0.0, min(float(stereo_width), 2.0))
    output.parent.mkdir(parents=True, exist_ok=True)

    filters = ["highpass=f=24", "lowpass=f=19500"]
    low_total = p.low_shelf_db * intensity + float(low_db)
    mid_total = p.mid_db * intensity + float(mid_db)
    high_total = p.high_shelf_db * intensity + float(high_db)
    if abs(low_total) >= .05:
        filters.append(f"bass=g={low_total:.3f}:f=120:w=0.7")
    if abs(mid_total) >= .05:
        filters.append(f"equalizer=f=2200:width_type=o:width=1:g={mid_total:.3f}")
    if abs(high_total) >= .05:
        filters.append(f"treble=g={high_total:.3f}:f=8500:w=0.6")
    ratio = 1.0 + (p.compression_ratio - 1.0) * intensity
    filters.append(f"acompressor=threshold=-18dB:ratio={ratio:.3f}:attack=18:release=180:makeup={1.2*intensity:.3f}dB")
    if abs(width - 1.0) > .01:
        filters.append(f"stereotools=mlev=1:slev={width:.3f}")
    filters += [
        f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11",
        f"alimiter=limit={10 ** (true_peak_db / 20.0):.5f}:attack=5:release=50",
    ]
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", ",".join(filters), "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ], check=True)
    report = {
        "method": "aura_adaptive_master",
        "preset": preset,
        "target_lufs": target_lufs,
        "true_peak_db": true_peak_db,
        "intensity": intensity,
        "low_db": low_db,
        "mid_db": mid_db,
        "high_db": high_db,
        "stereo_width": width,
        "fallback_reason": fallback_reason,
        "analysis": analyze_master(output),
    }
    return output, report


def master_album(
    sources: list[Path],
    output_dir: Path,
    *,
    preset: str = "natural",
    target_lufs: float | None = None,
    intensity: float = 1.0,
) -> list[dict]:
    """Master an album/EP with one shared tonal/loudness profile for cross-track consistency."""
    if not sources:
        raise ValueError("At least one source track is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    analyses = [analyze_master(p) for p in sources]
    existing_lufs = [x["integrated_lufs"] for x in analyses if x.get("integrated_lufs") is not None]
    p = PRESETS.get(preset, PRESETS["natural"])
    shared_lufs = float(target_lufs if target_lufs is not None else p.target_lufs)
    results = []
    for index, source in enumerate(sources, 1):
        out = output_dir / f"{index:02d}_{source.stem}_AuraAlbumMaster.wav"
        mastered, report = master(source, out, preset=preset, target_lufs=shared_lufs, intensity=intensity)
        results.append({"source": str(source), "output": str(mastered), "report": report})
    summary = {
        "preset": preset,
        "shared_target_lufs": shared_lufs,
        "source_lufs": existing_lufs,
        "tracks": results,
    }
    (output_dir / "Aura_Album_Mastering_Report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return results


def translation_report(path: Path) -> dict:
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
