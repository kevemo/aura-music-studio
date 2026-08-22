from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .session import AutomationLane, Track


def _curve(lane: AutomationLane | None, times: np.ndarray, default: float) -> np.ndarray:
    if not lane or not lane.points:
        return np.full_like(times, default, dtype=np.float32)
    pts = sorted(lane.points, key=lambda p: p.time)
    x = np.array([p.time for p in pts], dtype=np.float64)
    y = np.array([p.value for p in pts], dtype=np.float64)
    if len(x) == 1:
        return np.full_like(times, float(y[0]), dtype=np.float32)
    return np.interp(times, x, y, left=y[0], right=y[-1]).astype(np.float32)


def _find_lane(track: Track, *names: str) -> AutomationLane | None:
    wanted = {x.lower() for x in names}
    for lane in track.automation:
        if lane.parameter.lower() in wanted:
            return lane
    return None


def apply_track_automation(source: Path, output: Path, track: Track, expected_sample_rate: int = 48000) -> Path:
    """Bake continuous fader/pan automation into waveform audio.

    Volume lane values are dB. Pan values are -1 (left) to +1 (right). Track volume/pan are
    used as defaults outside automation points. This is waveform DSP and never renders MIDI.
    """
    audio, sr = sf.read(source, always_2d=True, dtype="float32")
    if sr != expected_sample_rate:
        raise RuntimeError(f"Automation input sample-rate mismatch: expected {expected_sample_rate}, got {sr}")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    elif audio.shape[1] > 2:
        audio = audio[:, :2]

    times = np.arange(len(audio), dtype=np.float64) / float(sr)
    vol_lane = _find_lane(track, "volume", "volume_db", "fader", "gain_db")
    pan_lane = _find_lane(track, "pan", "balance")
    volume_db = _curve(vol_lane, times, track.volume_db)
    pan = np.clip(_curve(pan_lane, times, track.pan), -1.0, 1.0)

    gain = np.power(10.0, volume_db / 20.0).astype(np.float32)
    # Preserve the existing stereo image while attenuating the side opposite the automation direction.
    left = np.where(pan > 0, np.sqrt(1.0 - pan), 1.0).astype(np.float32)
    right = np.where(pan < 0, np.sqrt(1.0 + pan), 1.0).astype(np.float32)
    audio[:, 0] *= gain * left
    audio[:, 1] *= gain * right

    # Prevent accidental integer overflow downstream; mastering retains responsibility for final level.
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, sr, subtype="FLOAT")
    return output


def automation_summary(track: Track) -> dict:
    return {
        lane.parameter: [{"time": p.time, "value": p.value} for p in sorted(lane.points, key=lambda p: p.time)]
        for lane in track.automation
    }
