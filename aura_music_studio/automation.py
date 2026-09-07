from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .session import AutomationLane, Track, normalize_automation_parameter


def automation_curve(lane: AutomationLane | None, times: np.ndarray, default: float) -> np.ndarray:
    """Evaluate one automation lane at sample times.

    ``hold`` creates stepped automation, ``linear`` preserves the historical renderer, and
    ``smooth`` uses a smoothstep transition between points without overshooting either value.
    Curves extend the first/last point to the file boundaries, matching normal DAW automation.
    """
    if not lane or not lane.points:
        return np.full_like(times, default, dtype=np.float32)
    pts = sorted(lane.points, key=lambda point: point.time)
    x = np.array([point.time for point in pts], dtype=np.float64)
    y = np.array([point.value for point in pts], dtype=np.float64)
    if len(x) == 1:
        return np.full_like(times, float(y[0]), dtype=np.float32)

    if lane.interpolation == "hold":
        indexes = np.searchsorted(x, times, side="right") - 1
        indexes = np.clip(indexes, 0, len(y) - 1)
        return y[indexes].astype(np.float32)

    if lane.interpolation == "smooth":
        indexes = np.searchsorted(x, times, side="right") - 1
        indexes = np.clip(indexes, 0, len(x) - 2)
        x0 = x[indexes]
        x1 = x[indexes + 1]
        span = np.maximum(1e-12, x1 - x0)
        phase = np.clip((times - x0) / span, 0.0, 1.0)
        eased = phase * phase * (3.0 - 2.0 * phase)
        values = y[indexes] + (y[indexes + 1] - y[indexes]) * eased
        values = np.where(times <= x[0], y[0], values)
        values = np.where(times >= x[-1], y[-1], values)
        return values.astype(np.float32)

    return np.interp(times, x, y, left=y[0], right=y[-1]).astype(np.float32)


# Historical private name retained for older tests/importers.
_curve = automation_curve


def find_automation_lane(track: Track, parameter: str, *aliases: str) -> AutomationLane | None:
    canonical, _bounds = normalize_automation_parameter(parameter)
    wanted = {canonical}
    for alias in aliases:
        normalized, _ = normalize_automation_parameter(alias)
        wanted.add(normalized)
    for lane in track.automation:
        lane_parameter, _ = normalize_automation_parameter(lane.parameter)
        if lane_parameter in wanted:
            return lane
    return None


def _stereo(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.shape[1] == 1:
        return np.repeat(audio, 2, axis=1)
    if audio.shape[1] > 2:
        return audio[:, :2]
    return audio


def apply_gain_automation(
    source: Path,
    output: Path,
    lane: AutomationLane | None,
    *,
    default_db: float,
    expected_sample_rate: int = 48000,
) -> Path:
    """Bake a dB automation curve into a real waveform file."""
    audio, sr = sf.read(source, always_2d=True, dtype="float32")
    if sr != expected_sample_rate:
        raise RuntimeError(f"Automation input sample-rate mismatch: expected {expected_sample_rate}, got {sr}")
    times = np.arange(len(audio), dtype=np.float64) / float(sr)
    gain_db = automation_curve(lane, times, float(default_db))
    gain = np.power(10.0, gain_db / 20.0).astype(np.float32)
    audio *= gain[:, None]
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, sr, subtype="FLOAT")
    return output


def blend_audio_with_mix_automation(
    dry_source: Path,
    wet_source: Path,
    output: Path,
    lane: AutomationLane | None,
    *,
    default_mix: float,
    expected_sample_rate: int = 48000,
) -> Path:
    """Crossfade dry/wet effect audio with static or time-varying mix automation."""
    dry, dry_sr = sf.read(dry_source, always_2d=True, dtype="float32")
    wet, wet_sr = sf.read(wet_source, always_2d=True, dtype="float32")
    if dry_sr != expected_sample_rate or wet_sr != expected_sample_rate:
        raise RuntimeError(
            f"Effect automation sample-rate mismatch: expected {expected_sample_rate}, got {dry_sr}/{wet_sr}"
        )
    frames = min(len(dry), len(wet))
    if frames <= 0:
        raise RuntimeError("Effect automation cannot blend empty audio")
    dry = _stereo(dry[:frames])
    wet = _stereo(wet[:frames])
    times = np.arange(frames, dtype=np.float64) / float(expected_sample_rate)
    mix = np.clip(automation_curve(lane, times, float(default_mix)), 0.0, 1.0)[:, None]
    audio = dry * (1.0 - mix) + wet * mix
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, expected_sample_rate, subtype="FLOAT")
    return output


def apply_track_automation(source: Path, output: Path, track: Track, expected_sample_rate: int = 48000) -> Path:
    """Bake continuous fader/pan automation into waveform audio.

    Volume lane values are dB. Pan values are -1 (left) to +1 (right). Track volume/pan are
    used as defaults outside automation points. This is waveform DSP and never renders MIDI.
    """
    audio, sr = sf.read(source, always_2d=True, dtype="float32")
    if sr != expected_sample_rate:
        raise RuntimeError(f"Automation input sample-rate mismatch: expected {expected_sample_rate}, got {sr}")
    audio = _stereo(audio)

    times = np.arange(len(audio), dtype=np.float64) / float(sr)
    vol_lane = find_automation_lane(track, "volume_db", "volume", "fader", "gain_db")
    pan_lane = find_automation_lane(track, "pan", "balance")
    volume_db = automation_curve(vol_lane, times, track.volume_db)
    pan = np.clip(automation_curve(pan_lane, times, track.pan), -1.0, 1.0)

    gain = np.power(10.0, volume_db / 20.0).astype(np.float32)
    # Preserve the existing stereo image while attenuating the side opposite the automation direction.
    left = np.where(pan > 0, np.sqrt(1.0 - pan), 1.0).astype(np.float32)
    right = np.where(pan < 0, np.sqrt(1.0 + pan), 1.0).astype(np.float32)
    audio[:, 0] *= gain * left
    audio[:, 1] *= gain * right

    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, sr, subtype="FLOAT")
    return output


def automation_summary(track: Track) -> dict:
    return {
        lane.parameter: {
            "interpolation": lane.interpolation,
            "points": [{"time": point.time, "value": point.value} for point in lane.points],
        }
        for lane in track.automation
    }


__all__ = [
    "apply_gain_automation",
    "apply_track_automation",
    "automation_curve",
    "automation_summary",
    "blend_audio_with_mix_automation",
    "find_automation_lane",
]
