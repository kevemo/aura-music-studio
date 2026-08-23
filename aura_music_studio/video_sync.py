from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import librosa
import numpy as np


def audio_beats(audio_path: str | Path) -> dict:
    source = Path(audio_path).resolve()
    y, sr = librosa.load(source, sr=None, mono=True)
    if y.size == 0:
        raise ValueError("Audio is empty")
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sr).astype(float).tolist()
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=False).astype(float).tolist()
    tempo_value = float(np.asarray(tempo).reshape(-1)[0]) if np.asarray(tempo).size else 0.0
    return {"tempo_bpm": tempo_value, "beat_times": beats, "onset_times": onsets}


def video_scene_cuts(video_path: str | Path, *, threshold: float = 0.35) -> list[float]:
    """Detect scene-cut timestamps locally with FFmpeg scene scores."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for local video scene-cut analysis")
    source = Path(video_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    vf = f"select='gt(scene,{float(threshold)})',showinfo"
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(source), "-vf", vf, "-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
    values = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", combined):
        value = float(match.group(1))
        if not values or abs(value - values[-1]) > 0.05:
            values.append(value)
    return values


def build_sync_map(
    video_path: str | Path,
    audio_path: str | Path,
    output_json: str | Path | None = None,
    *,
    scene_threshold: float = 0.35,
    snap_window_seconds: float = 0.35,
) -> dict:
    musical = audio_beats(audio_path)
    cuts = video_scene_cuts(video_path, threshold=scene_threshold)
    beats = musical["beat_times"]
    onsets = musical["onset_times"]

    suggestions = []
    for cut in cuts:
        candidates = [(abs(t - cut), t, "beat") for t in beats]
        candidates += [(abs(t - cut), t, "onset") for t in onsets]
        if candidates:
            distance, musical_time, kind = min(candidates, key=lambda item: item[0])
            suggestions.append({
                "scene_cut_seconds": cut,
                "nearest_musical_event_seconds": musical_time,
                "event_type": kind,
                "offset_seconds": musical_time - cut,
                "within_snap_window": distance <= snap_window_seconds,
            })

    result = {
        "video": str(Path(video_path)),
        "audio": str(Path(audio_path)),
        "tempo_bpm": musical["tempo_bpm"],
        "scene_cuts": cuts,
        "beat_times": beats,
        "onset_times": onsets,
        "sync_suggestions": suggestions,
        "note": (
            "This produces an editable timing map. Aura can use these markers to request real-audio "
            "repaint/arrangement changes; the map itself is not rendered music."
        ),
    }
    if output_json is not None:
        target = Path(output_json).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
