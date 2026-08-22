from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from .models import AnalysisResult, ProjectManifest
from .project import ProjectWorkspace


PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _estimate_key(y: np.ndarray, sr: int) -> str | None:
    if y.size == 0:
        return None
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)
    if not np.any(np.isfinite(profile)):
        return None
    root = int(np.nanargmax(profile))
    major_third = profile[(root + 4) % 12]
    minor_third = profile[(root + 3) % 12]
    quality = "Major" if major_third >= minor_third else "Minor"
    return f"{PITCH_NAMES[root]} {quality}"


def analyze_audio(path: Path) -> AnalysisResult:
    y, sr = librosa.load(path, sr=None, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo_value = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else None
    return AnalysisResult(
        tempo_bpm=tempo_value,
        key=_estimate_key(y, sr),
        duration_seconds=float(librosa.get_duration(y=y, sr=sr)),
        sample_rate=int(sr),
        beats=[float(x) for x in np.atleast_1d(beats)[:1000]],
        source="audio_analysis",
    )


def analyze_score(path: Path) -> dict:
    """Read MIDI/MusicXML when available. PDF notation is treated as a human/reference asset."""
    suffix = path.suffix.lower()
    if suffix not in {".mid", ".midi", ".xml", ".musicxml", ".mxl"}:
        return {"score_read": False, "reason": "PDF/image score requires a supplied transcription or MIDI/MusicXML."}
    from music21 import converter, key as m21key, meter, tempo

    score = converter.parse(str(path))
    bpm = None
    mm = score.recurse().getElementsByClass(tempo.MetronomeMark)
    if mm:
        bpm = float(mm[0].number) if mm[0].number else None
    keys = score.recurse().getElementsByClass(m21key.Key)
    k = str(keys[0]) if keys else None
    sigs = score.recurse().getElementsByClass(meter.TimeSignature)
    meter_value = sigs[0].ratioString if sigs else None
    measures = score.recurse().getElementsByClass("Measure")
    return {
        "score_read": True,
        "tempo_bpm": bpm,
        "key": k,
        "meter": meter_value,
        "measure_count": len(measures),
    }


def analyze_project(workspace: ProjectWorkspace, manifest: ProjectManifest) -> AnalysisResult:
    reference = workspace.resolve_asset(manifest.reference_audio)
    if reference and reference.exists():
        result = analyze_audio(reference)
    else:
        result = AnalysisResult(
            tempo_bpm=manifest.tempo_bpm,
            key=manifest.key,
            source="manifest",
            notes=["No reference audio supplied; using manifest timing."],
        )

    score_value = manifest.musicxml_file or manifest.midi_file or manifest.score_file
    score = workspace.resolve_asset(score_value)
    if score and score.exists():
        score_info = analyze_score(score)
        if score_info.get("score_read"):
            if not result.tempo_bpm and score_info.get("tempo_bpm"):
                result.tempo_bpm = score_info["tempo_bpm"]
            if not result.key and score_info.get("key"):
                result.key = score_info["key"]
            result.notes.append(f"Score: {score_info}")
        else:
            result.notes.append(score_info["reason"])

    if manifest.tempo_bpm:
        result.tempo_bpm = manifest.tempo_bpm
    if manifest.key:
        result.key = manifest.key
    return result
