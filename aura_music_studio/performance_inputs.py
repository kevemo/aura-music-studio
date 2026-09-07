from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import librosa
import mido
import numpy as np
import yaml
from pydantic import BaseModel, Field

from .transcription import audio_to_midi, transcription_metadata

PerformanceInputKind = Literal[
    "rhythm",
    "beatbox",
    "hum",
    "melody",
    "instrument",
    "voice_memo",
    "reference_audio",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float(value, default: float = 0.0) -> float:
    try:
        arr = np.asarray(value).reshape(-1)
        return float(arr[0]) if arr.size else default
    except Exception:
        return default


class PerformanceInput(BaseModel):
    id: str = Field(default_factory=lambda: f"guide_{uuid4().hex}")
    kind: PerformanceInputKind
    label: str = ""
    intent: str = ""
    source_ref: str
    rights_confirmed: bool = True
    duration_seconds: float = 0.0
    sample_rate: int = 0
    detected_bpm: float | None = None
    beat_times_seconds: list[float] = Field(default_factory=list)
    onset_times_seconds: list[float] = Field(default_factory=list)
    pitch_class_hint: str | None = None
    midi_ref: str | None = None
    status: Literal["analysed", "applied", "failed"] = "analysed"
    generation_context: str = ""
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class PerformanceInputManifest(BaseModel):
    schema_version: int = 1
    inputs: list[PerformanceInput] = Field(default_factory=list)


_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _relative(project: Path, value: Path) -> str:
    return str(value.resolve().relative_to(project.resolve())).replace("\\", "/")


def _safe_source(project: Path, source_ref: str) -> Path:
    root = project.resolve()
    target = (root / source_ref).resolve()
    if root not in target.parents:
        raise ValueError("Performance input escaped the project boundary")
    if not target.is_file():
        raise FileNotFoundError(source_ref)
    return target


def _manifest_path(project: Path) -> Path:
    return project / "performance_inputs.json"


def load_manifest(project: Path) -> PerformanceInputManifest:
    path = _manifest_path(project)
    if not path.is_file():
        return PerformanceInputManifest()
    return PerformanceInputManifest.model_validate_json(path.read_text(encoding="utf-8"))


def save_manifest(project: Path, manifest: PerformanceInputManifest) -> None:
    path = _manifest_path(project)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def get_input(project: Path, input_id: str) -> PerformanceInput:
    item = next((row for row in load_manifest(project).inputs if row.id == input_id), None)
    if item is None:
        raise KeyError(input_id)
    return item


def rhythm_to_midi(
    y: np.ndarray,
    sr: int,
    output_midi: Path,
    *,
    bpm: float,
    onset_frames: np.ndarray | None = None,
    hop_length: int = 512,
) -> Path:
    """Create a groove-guide MIDI track from detected audio onsets.

    This is deliberately a guide, never final audio. Onsets are loosely classified by
    spectral centroid into kick/snare/hat lanes so a beatbox or tapped rhythm can drive
    later drum/instrument generation while the original timing remains available.
    """
    output_midi.parent.mkdir(parents=True, exist_ok=True)
    if onset_frames is None:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop_length, units="frames")
    onset_frames = np.asarray(onset_frames, dtype=int)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

    mf = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mf.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Aura Rhythm Guide", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(max(30.0, min(300.0, bpm))), time=0))
    seconds_per_tick = (60.0 / max(30.0, min(300.0, bpm))) / mf.ticks_per_beat
    events: list[tuple[float, bool, int, int]] = []
    for frame, sec in zip(onset_frames, onset_times):
        idx = int(max(0, min(frame, len(centroid) - 1)))
        hz = float(centroid[idx]) if len(centroid) else 0.0
        note = 36 if hz < 1100 else 38 if hz < 3000 else 42
        strength = float(onset_env[min(idx, len(onset_env) - 1)]) if len(onset_env) else 1.0
        velocity = int(max(40, min(120, 52 + strength * 12)))
        start = float(sec)
        end = start + 0.055
        events.extend([(start, True, note, velocity), (end, False, note, 0)])
    events.sort(key=lambda row: (row[0], not row[1]))
    last_tick = 0
    for sec, on, note, velocity in events:
        tick = max(last_tick, round(sec / seconds_per_tick))
        track.append(
            mido.Message(
                "note_on" if on else "note_off",
                channel=9,
                note=note,
                velocity=velocity,
                time=tick - last_tick,
            )
        )
        last_tick = tick
    mf.save(output_midi)
    return output_midi


def analyse_performance_input(
    project: Path,
    *,
    source_ref: str,
    kind: PerformanceInputKind,
    label: str = "",
    intent: str = "",
    input_id: str | None = None,
) -> PerformanceInput:
    source = _safe_source(project, source_ref)
    y, sr = librosa.load(source, sr=None, mono=True)
    if y.size == 0:
        raise ValueError("Uploaded audio is empty")
    duration = float(librosa.get_duration(y=y, sr=sr))
    hop = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop)
    bpm = max(30.0, min(300.0, _float(tempo, 120.0)))
    beat_times = librosa.frames_to_time(np.asarray(beat_frames), sr=sr, hop_length=hop)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop, units="frames")
    onset_times = librosa.frames_to_time(np.asarray(onset_frames), sr=sr, hop_length=hop)

    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop)
    pitch_class_hint = None
    if chroma.size:
        pitch_class_hint = _PITCH_CLASSES[int(np.argmax(np.mean(chroma, axis=1))) % 12]

    item = PerformanceInput(
        id=input_id or f"guide_{uuid4().hex}",
        kind=kind,
        label=(label or source.stem)[:160],
        intent=(intent or "")[:1500],
        source_ref=source_ref,
        duration_seconds=round(duration, 4),
        sample_rate=int(sr),
        detected_bpm=round(bpm, 3),
        beat_times_seconds=[round(float(x), 4) for x in beat_times[:4000]],
        onset_times_seconds=[round(float(x), 4) for x in onset_times[:8000]],
        pitch_class_hint=pitch_class_hint,
        metadata={
            "analysis_engine": "librosa",
            "onset_count": int(len(onset_times)),
            "beat_count": int(len(beat_times)),
            "symbolic_is_guide_only": True,
        },
    )

    midi_dir = project / "input" / "performance_guides" / "transcriptions"
    midi_path = midi_dir / f"{item.id}.mid"
    if kind in {"rhythm", "beatbox"}:
        rhythm_to_midi(y, sr, midi_path, bpm=bpm, onset_frames=np.asarray(onset_frames), hop_length=hop)
        item.midi_ref = _relative(project, midi_path)
        item.metadata.update({
            "midi_transcription_mode": "rhythm_onset_guide",
            "midi_symbolic_guide_only": True,
            "midi_final_audio": False,
        })
        item.generation_context = (
            f"Use performance guide {item.id} as the rhythmic/groove anchor. Preserve its human onset pattern and feel; "
            f"detected working tempo is approximately {bpm:.2f} BPM. Build realistic performed instruments around the groove rather than quantising away its character."
        )
    elif kind in {"hum", "melody", "instrument"}:
        try:
            # Hums and explicit melody guides are bounded to one dominant pitched line. An
            # instrument guide may use the optional polyphonic-capable runtime when present,
            # while auto mode still fails back to truthful monophonic pYIN transcription.
            mode = "monophonic" if kind in {"hum", "melody"} else "auto"
            audio_to_midi(source, midi_path, bpm=bpm, mode=mode)
            report = transcription_metadata(midi_path)
            if report and report.get("source_sha256") != _sha256_for_metadata(source):
                raise RuntimeError("Transcription provenance does not match the source audio")
            item.midi_ref = _relative(project, midi_path)
            item.metadata.update({
                "midi_transcription": report,
                "midi_transcription_mode": report.get("mode_requested", mode) if report else mode,
                "midi_transcription_engine": report.get("engine") if report else None,
                "midi_output_sha256": report.get("output_sha256") if report else None,
                "midi_note_count": report.get("note_count") if report else None,
                "midi_symbolic_guide_only": True,
                "midi_final_audio": False,
            })
        except Exception as exc:
            item.metadata["midi_warning"] = f"{type(exc).__name__}: {exc}"
        if kind in {"hum", "melody"}:
            item.generation_context = (
                f"Use performance guide {item.id} as a melody/phrase anchor. Preserve its musical contour, phrasing and rhythm while arranging a professional full song around it. "
                f"Working tempo is approximately {bpm:.2f} BPM; pitch-class centre hint is {pitch_class_hint or 'unknown'}."
            )
        else:
            item.generation_context = (
                f"Preserve the uploaded {item.label} performance as a musical anchor and build the arrangement around its timing, harmony, dynamics and groove. "
                f"Working tempo is approximately {bpm:.2f} BPM. Do not replace the source performance unless the user later asks to."
            )
    elif kind == "voice_memo":
        item.generation_context = (
            f"Treat performance guide {item.id} as a composition reference. Analyse its phrasing/rhythm and use it to guide the song while keeping the final production fully editable."
        )
    else:
        item.generation_context = (
            f"Use performance guide {item.id} only as a user-authorised sonic/reference guide. Do not copy protected material verbatim; preserve only requested high-level musical attributes."
        )
    return item


def _sha256_for_metadata(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_input(project: Path, item: PerformanceInput) -> PerformanceInput:
    manifest = load_manifest(project)
    existing = next((i for i, row in enumerate(manifest.inputs) if row.id == item.id), None)
    if existing is None:
        manifest.inputs.append(item)
    else:
        manifest.inputs[existing] = item
    save_manifest(project, manifest)
    return item


def apply_input_to_project(project: Path, input_id: str) -> PerformanceInput:
    manifest = load_manifest(project)
    item = next((row for row in manifest.inputs if row.id == input_id), None)
    if item is None:
        raise KeyError(input_id)
    project_yaml = project / "project.yaml"
    if not project_yaml.is_file():
        raise FileNotFoundError("project.yaml")
    payload = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    dna = payload.setdefault("project_dna", {})
    guides = list(dna.get("performance_inputs") or [])
    guides = [row for row in guides if row.get("id") != item.id]
    guides.append({
        "id": item.id,
        "kind": item.kind,
        "source_ref": item.source_ref,
        "midi_ref": item.midi_ref,
        "detected_bpm": item.detected_bpm,
        "generation_context": item.generation_context,
    })
    dna["performance_inputs"] = guides[-20:]
    if not payload.get("tempo_bpm") and item.detected_bpm:
        payload["tempo_bpm"] = item.detected_bpm
    prompt = str(payload.get("prompt") or "")
    marker = f"Performance guide {item.id}:"
    if marker not in prompt:
        payload["prompt"] = (prompt + ". " + marker + " " + item.generation_context).strip(". ")
    tmp = project_yaml.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(project_yaml)
    item.status = "applied"
    item.updated_at = _now()
    save_manifest(project, manifest)
    return item
