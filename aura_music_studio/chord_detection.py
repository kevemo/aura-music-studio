from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import librosa
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .chord_intelligence import ChordEvent, _persist_progression, _store, describe_chord
from .plans import HARMONY_ARCHITECT
from .tenant_storage import project_path

router = APIRouter(tags=["Chord Intelligence"])

DETECTION_ENGINE = "librosa_chroma_template_v1"
ANALYSIS_SAMPLE_RATE = 22050
MAX_ANALYSIS_SECONDS = 30.0 * 60.0
MAX_SOURCE_BYTES = 512 * 1024 * 1024
CANDIDATE_TTL = timedelta(days=7)
ALLOWED_AUDIO_SUFFIXES = frozenset({".wav", ".wave", ".flac", ".mp3", ".ogg", ".m4a", ".aac", ".aif", ".aiff"})
PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class ChordDetectionPreviewRequest(BaseModel):
    source_ref: str = Field(min_length=1, max_length=1000)
    hop_length: int = Field(default=1024, ge=512, le=4096)
    minimum_segment_seconds: float = Field(default=0.45, ge=0.2, le=4.0)
    confidence_threshold: float = Field(default=0.45, ge=0.05, le=0.95)
    max_events: int = Field(default=256, ge=1, le=512)
    key_hint: str | None = Field(default=None, max_length=24)


class ChordDetectionCommitRequest(BaseModel):
    candidate_id: str = Field(pattern=r"^chdet_[a-f0-9]{32}$")
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    key: str | None = Field(default=None, max_length=24)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key != "candidate_sha256"}
    encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(HARMONY_ARCHITECT):
        raise HTTPException(403, "Audio chord detection unlocks with Harmony Architect on Basic or Pro")
    return member


def safe_project_audio_source(project: Path, source_ref: str) -> Path:
    """Resolve an audio source strictly inside one tenant-scoped project."""
    raw = Path(str(source_ref or "").strip())
    if not str(raw) or raw.is_absolute():
        raise ValueError("Audio source must be a project-relative path")
    root = project.resolve()
    target = (root / raw).resolve()
    if target == root or root not in target.parents:
        raise ValueError("Audio source escaped the project boundary")
    if not target.is_file():
        raise FileNotFoundError(source_ref)
    if target.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        raise ValueError("Unsupported audio source type for chord detection")
    size = target.stat().st_size
    if size <= 0:
        raise ValueError("Audio source is empty")
    if size > MAX_SOURCE_BYTES:
        raise ValueError("Audio source is too large for bounded chord detection")
    return target


def _templates() -> tuple[np.ndarray, list[str]]:
    rows: list[np.ndarray] = []
    labels: list[str] = []
    for root in range(12):
        for quality, intervals in (("", (0, 4, 7)), ("m", (0, 3, 7))):
            vector = np.zeros(12, dtype=float)
            for interval, weight in zip(intervals, (1.0, 0.8, 0.8)):
                vector[(root + interval) % 12] = weight
            vector /= max(float(np.linalg.norm(vector)), 1e-9)
            rows.append(vector)
            labels.append(PITCH_CLASS_NAMES[root] + quality)
    return np.asarray(rows, dtype=float), labels


_CHORD_TEMPLATES, _CHORD_LABELS = _templates()


def _smooth_labels(labels: list[str | None], radius: int) -> list[str | None]:
    if radius <= 0 or len(labels) < 3:
        return list(labels)
    output: list[str | None] = []
    for index, current in enumerate(labels):
        if current is None:
            output.append(None)
            continue
        start = max(0, index - radius)
        end = min(len(labels), index + radius + 1)
        votes = Counter(value for value in labels[start:end] if value is not None)
        output.append(votes.most_common(1)[0][0] if votes else current)
    return output


def detect_chord_segments(
    y: np.ndarray,
    sr: int,
    *,
    hop_length: int = 1024,
    minimum_segment_seconds: float = 0.45,
    confidence_threshold: float = 0.45,
    max_events: int = 256,
) -> list[dict[str, Any]]:
    """Detect bounded major/minor triad segments from real audio chroma.

    This deliberately claims only 24 common major/minor triad templates. It is a local,
    deterministic analysis path and does not pretend to recognise every extended chord,
    inversion or ambiguous harmonic passage.
    """
    samples = np.asarray(y, dtype=float).reshape(-1)
    if samples.size == 0:
        raise ValueError("Chord detection source is empty")
    if not np.all(np.isfinite(samples)):
        samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(samples)))
    if peak <= 1e-6:
        raise ValueError("Chord detection source is silent")
    if sr <= 0:
        raise ValueError("Invalid audio sample rate")

    n_fft = 4096
    chroma = librosa.feature.chroma_stft(
        y=samples,
        sr=int(sr),
        hop_length=int(hop_length),
        n_fft=n_fft,
        tuning=0.0,
    )
    rms = librosa.feature.rms(y=samples, frame_length=n_fft, hop_length=int(hop_length), center=True)[0]
    frame_count = min(chroma.shape[1], len(rms))
    if frame_count <= 0:
        raise RuntimeError("Chord analysis produced no frames")
    chroma = chroma[:, :frame_count]
    rms = np.asarray(rms[:frame_count], dtype=float)

    norms = np.linalg.norm(chroma, axis=0)
    normalized = chroma / np.maximum(norms, 1e-9)
    scores = _CHORD_TEMPLATES @ normalized
    best_indices = np.argmax(scores, axis=0)
    ordered_scores = np.sort(scores, axis=0)
    best_scores = ordered_scores[-1]
    second_scores = ordered_scores[-2]
    margins = np.maximum(0.0, best_scores - second_scores)
    confidence = np.clip(0.65 * best_scores + 0.35 * margins, 0.0, 1.0)

    energy_floor = max(float(np.max(rms)) * 0.02, 1e-6)
    labels: list[str | None] = []
    for index in range(frame_count):
        if rms[index] < energy_floor or confidence[index] < float(confidence_threshold):
            labels.append(None)
        else:
            labels.append(_CHORD_LABELS[int(best_indices[index])])

    smoothing_radius = max(1, min(8, round(0.22 * int(sr) / int(hop_length))))
    labels = _smooth_labels(labels, smoothing_radius)
    frame_seconds = float(hop_length) / float(sr)

    segments: list[dict[str, Any]] = []
    active_label: str | None = None
    active_start = 0

    def close_segment(end_index: int) -> None:
        nonlocal active_label, active_start
        if active_label is None:
            return
        start_seconds = active_start * frame_seconds
        end_seconds = min(samples.size / float(sr), max(start_seconds + frame_seconds, end_index * frame_seconds))
        duration = end_seconds - start_seconds
        if duration + 1e-9 < float(minimum_segment_seconds):
            return
        values = confidence[active_start:max(active_start + 1, end_index)]
        mean_confidence = float(np.mean(values)) if values.size else 0.0
        segments.append(
            {
                "symbol": active_label,
                "start_seconds": round(start_seconds, 4),
                "end_seconds": round(end_seconds, 4),
                "confidence": round(mean_confidence, 4),
            }
        )

    for index, label in enumerate(labels):
        if label == active_label:
            continue
        close_segment(index)
        active_label = label
        active_start = index
    close_segment(frame_count)

    if not segments:
        raise RuntimeError("No stable major/minor chord segments met the detection threshold")
    if len(segments) > int(max_events):
        raise RuntimeError(
            f"Detected {len(segments)} chord segments, above the bounded maximum of {int(max_events)}; "
            "increase minimum_segment_seconds or analyse a shorter source"
        )
    return segments


def detect_chords_from_file(
    source: Path,
    *,
    hop_length: int = 1024,
    minimum_segment_seconds: float = 0.45,
    confidence_threshold: float = 0.45,
    max_events: int = 256,
) -> dict[str, Any]:
    source = Path(source)
    y, sr = librosa.load(
        str(source),
        sr=ANALYSIS_SAMPLE_RATE,
        mono=True,
        duration=MAX_ANALYSIS_SECONDS + 1.0,
    )
    if y.size == 0:
        raise ValueError("Chord detection source is empty")
    duration = float(len(y)) / float(sr)
    if duration > MAX_ANALYSIS_SECONDS:
        raise ValueError(f"Chord detection is limited to {int(MAX_ANALYSIS_SECONDS // 60)} minutes per analysis")
    segments = detect_chord_segments(
        y,
        int(sr),
        hop_length=hop_length,
        minimum_segment_seconds=minimum_segment_seconds,
        confidence_threshold=confidence_threshold,
        max_events=max_events,
    )
    return {
        "segments": segments,
        "duration_seconds": round(duration, 4),
        "sample_rate": int(sr),
        "engine": DETECTION_ENGINE,
        "quality_scope": "major_minor_triads",
    }


def _candidate_directory(project: Path) -> Path:
    folder = project / "work" / "chord_detection"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _candidate_path(project: Path, candidate_id: str) -> Path:
    return _candidate_directory(project) / f"{candidate_id}.json"


def _write_candidate(project: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["candidate_sha256"] = _canonical_sha256(payload)
    path = _candidate_path(project, str(payload["candidate_id"]))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def _load_candidate(project: Path, candidate_id: str, expected_sha256: str) -> dict[str, Any]:
    path = _candidate_path(project, candidate_id)
    if not path.is_file():
        raise FileNotFoundError(candidate_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("Stored chord detection candidate is invalid") from exc
    if not isinstance(payload, dict) or payload.get("candidate_id") != candidate_id:
        raise ValueError("Stored chord detection candidate identity is invalid")
    stored_sha = str(payload.get("candidate_sha256") or "")
    computed_sha = _canonical_sha256(payload)
    if stored_sha != computed_sha or stored_sha != expected_sha256:
        raise ValueError("Chord detection candidate integrity check failed")
    try:
        created = datetime.fromisoformat(str(payload["created_at"]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise ValueError("Chord detection candidate timestamp is invalid") from exc
    if datetime.now(timezone.utc) - created > CANDIDATE_TTL:
        raise ValueError("Chord detection candidate expired; create a fresh preview")
    return payload


@router.post("/projects/{project_name}/song-dna/chords/detect-preview")
def preview_audio_chords(project_name: str, body: ChordDetectionPreviewRequest, request: Request):
    _member(request)
    project = _project(project_name)
    try:
        source = safe_project_audio_source(project, body.source_ref)
        source_sha = _sha256(source)
        analysis = detect_chords_from_file(
            source,
            hop_length=body.hop_length,
            minimum_segment_seconds=body.minimum_segment_seconds,
            confidence_threshold=body.confidence_threshold,
            max_events=body.max_events,
        )
        candidate_id = f"chdet_{uuid4().hex}"
        events: list[ChordEvent] = []
        for index, segment in enumerate(analysis["segments"]):
            events.append(
                ChordEvent(
                    id=f"{candidate_id}_{index:04d}",
                    symbol=str(segment["symbol"]),
                    start_seconds=float(segment["start_seconds"]),
                    end_seconds=float(segment["end_seconds"]),
                    source="detected",
                    metadata={
                        "detection_engine": DETECTION_ENGINE,
                        "detection_confidence": float(segment["confidence"]),
                        "detection_quality_scope": "major_minor_triads",
                        "source_ref": body.source_ref,
                        "source_sha256": source_sha,
                        "candidate_id": candidate_id,
                    },
                )
            )
        payload = _write_candidate(
            project,
            {
                "schema_version": 1,
                "candidate_id": candidate_id,
                "created_at": _now(),
                "source_ref": body.source_ref,
                "source_sha256": source_sha,
                "duration_seconds": analysis["duration_seconds"],
                "sample_rate": analysis["sample_rate"],
                "engine": DETECTION_ENGINE,
                "provider_invoked": False,
                "quality_scope": "major_minor_triads",
                "key_hint": body.key_hint,
                "parameters": {
                    "hop_length": body.hop_length,
                    "minimum_segment_seconds": body.minimum_segment_seconds,
                    "confidence_threshold": body.confidence_threshold,
                    "max_events": body.max_events,
                },
                "events": [event.model_dump(mode="json") for event in events],
            },
        )
        return {
            **payload,
            "analysis": [describe_chord(event.symbol, body.key_hint).model_dump(mode="json") for event in events],
            "song_dna_mutated": False,
            "audio_rendered": False,
            "midi_rewritten": False,
            "commit_required": True,
            "detail": "Detected chord candidates are a preview only. Song DNA is unchanged until the exact candidate is committed.",
        }
    except FileNotFoundError as exc:
        raise HTTPException(404, "Audio source not found in this project") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_name}/song-dna/chords/detect-commit")
def commit_audio_chords(project_name: str, body: ChordDetectionCommitRequest, request: Request):
    _member(request)
    project = _project(project_name)
    try:
        candidate = _load_candidate(project, body.candidate_id, body.candidate_sha256)
        source = safe_project_audio_source(project, str(candidate["source_ref"]))
        source_sha = _sha256(source)
        if source_sha != candidate.get("source_sha256"):
            raise ValueError("Source audio changed after detection; create a fresh preview before committing")
        events = [ChordEvent.model_validate(item) for item in candidate.get("events") or []]
        if not events:
            raise ValueError("Chord detection candidate has no events")
        if any(event.source != "detected" for event in events):
            raise ValueError("Chord detection candidate contains a non-detected chord event")
        result = _persist_progression(
            _store(project_name),
            events,
            key=body.key or candidate.get("key_hint"),
            operation="commit_detected_audio_chords",
        )
        result.update(
            {
                "candidate_id": body.candidate_id,
                "candidate_sha256": body.candidate_sha256,
                "source_ref": candidate["source_ref"],
                "source_sha256": source_sha,
                "detection_engine": candidate["engine"],
                "detection_quality_scope": candidate["quality_scope"],
                "provider_invoked": False,
                "song_dna_mutated": True,
                "committed_detection_preview": True,
            }
        )
        return result
    except FileNotFoundError as exc:
        raise HTTPException(404, "Chord detection candidate or source audio was not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


__all__ = [
    "ANALYSIS_SAMPLE_RATE",
    "DETECTION_ENGINE",
    "MAX_ANALYSIS_SECONDS",
    "ChordDetectionCommitRequest",
    "ChordDetectionPreviewRequest",
    "detect_chord_segments",
    "detect_chords_from_file",
    "router",
    "safe_project_audio_source",
]
