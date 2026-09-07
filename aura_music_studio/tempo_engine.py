from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import librosa
import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field


TEMPO_ENGINE_VERSION = "aura-smart-warp-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_project_file(project: Path, ref: str) -> Path:
    root = project.resolve()
    target = (root / ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Tempo source escaped the member project") from exc
    if not target.is_file():
        raise FileNotFoundError(ref)
    return target


def _relative(project: Path, path: Path) -> str:
    root = project.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Tempo artifact escaped the member project") from exc


class TempoAnchor(BaseModel):
    beat_index: int = Field(ge=0)
    time_seconds: float = Field(ge=0.0)
    bpm: float = Field(ge=20.0, le=400.0)
    confidence: float = Field(ge=0.0, le=1.0)
    bar_index: int = Field(ge=0)
    beat_in_bar: int = Field(ge=1)
    source: Literal["beat", "onset", "fixed_grid"] = "beat"


class VariableTempoMap(BaseModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: f"tempo_{uuid4().hex}")
    source_ref: str
    source_sha256: str
    sample_rate: int = Field(gt=0)
    duration_seconds: float = Field(gt=0.0)
    working_bpm: float = Field(ge=20.0, le=400.0)
    variable: bool
    meter_numerator: int = Field(default=4, ge=1, le=32)
    meter_denominator: int = Field(default=4, ge=1, le=32)
    anchors: list[TempoAnchor] = Field(min_length=4)
    beat_times_seconds: list[float] = Field(min_length=4)
    analysis_engine: str = TEMPO_ENGINE_VERSION
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class TempoConformReport(BaseModel):
    engine: str = TEMPO_ENGINE_VERSION
    real_audio: bool = True
    pitch_preserving_algorithm: bool = True
    destructive_source_edit: bool = False
    source_sha256_before: str
    source_sha256_after: str
    target_tempo_map_id: str
    source_duration_seconds: float
    output_duration_seconds: float
    sample_rate: int
    channels: int
    segment_count: int
    duration_scale_min: float
    duration_scale_max: float
    duration_scale_mean: float
    output_peak: float
    output_rms: float
    source_beats_used: int
    target_beats_used: int


def _clean_beat_times(values: list[float] | np.ndarray, *, duration: float) -> np.ndarray:
    rows = sorted({round(float(value), 6) for value in values if np.isfinite(value)})
    clean: list[float] = []
    for value in rows:
        if value < 0.0 or value > duration + 0.05:
            continue
        if clean and value - clean[-1] < 0.08:
            continue
        clean.append(max(0.0, min(duration, value)))
    return np.asarray(clean, dtype=float)


def _smooth_local_bpms(raw: np.ndarray) -> np.ndarray:
    if raw.size == 0:
        return raw
    padded = np.pad(raw, (1, 1), mode="edge")
    median3 = np.asarray([np.median(padded[i : i + 3]) for i in range(raw.size)], dtype=float)
    smoothed = raw * 0.35 + median3 * 0.65
    # Suppress detector spikes while retaining intentional rubato and gradual tempo movement.
    max_fractional_jump = 0.22
    for index in range(1, smoothed.size):
        previous = max(20.0, smoothed[index - 1])
        lower = previous * (1.0 - max_fractional_jump)
        upper = previous * (1.0 + max_fractional_jump)
        smoothed[index] = float(np.clip(smoothed[index], lower, upper))
    for index in range(smoothed.size - 2, -1, -1):
        following = max(20.0, smoothed[index + 1])
        lower = following * (1.0 - max_fractional_jump)
        upper = following * (1.0 + max_fractional_jump)
        smoothed[index] = float(np.clip(smoothed[index], lower, upper))
    return np.clip(smoothed, 20.0, 400.0)


def tempo_map_from_beats(
    beat_times_seconds: list[float] | np.ndarray,
    *,
    source_ref: str,
    source_sha256: str,
    sample_rate: int,
    duration_seconds: float,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    source_kind: Literal["beat", "onset", "fixed_grid"] = "beat",
    metadata: dict | None = None,
) -> VariableTempoMap:
    beats = _clean_beat_times(beat_times_seconds, duration=duration_seconds)
    if beats.size < 4:
        raise ValueError("Smart Warp requires at least four reliable timing anchors")
    intervals = np.diff(beats)
    valid = (intervals >= 0.15) & (intervals <= 3.0)
    if int(np.count_nonzero(valid)) < 3:
        raise ValueError("Smart Warp could not derive a reliable musical beat grid")

    # Keep the full monotonic beat grid, but clamp pathological local detector intervals into the
    # professional tempo range before smoothing so a single false transient cannot jerk the map.
    raw_bpms = np.clip(60.0 / np.maximum(intervals, 0.15), 20.0, 400.0)
    local_bpms = _smooth_local_bpms(raw_bpms)
    working_bpm = float(np.median(local_bpms))
    p10, p90 = np.percentile(local_bpms, [10, 90]) if local_bpms.size > 1 else (working_bpm, working_bpm)
    variability = float(np.std(local_bpms) / max(working_bpm, 1e-6))
    is_variable = bool((p90 - p10) >= 4.0 or variability >= 0.025)

    anchors: list[TempoAnchor] = []
    for index, beat_time in enumerate(beats):
        if index == 0:
            bpm = float(local_bpms[0])
            reference = float(raw_bpms[0])
        elif index >= local_bpms.size:
            bpm = float(local_bpms[-1])
            reference = float(raw_bpms[-1])
        else:
            bpm = float((local_bpms[index - 1] + local_bpms[index]) / 2.0)
            reference = float((raw_bpms[index - 1] + raw_bpms[index]) / 2.0)
        relative_error = abs(reference - bpm) / max(bpm, 1e-6)
        confidence = float(np.clip(1.0 - relative_error * 1.8, 0.2, 1.0))
        anchors.append(
            TempoAnchor(
                beat_index=index,
                time_seconds=round(float(beat_time), 6),
                bpm=round(bpm, 4),
                confidence=round(confidence, 4),
                bar_index=index // meter_numerator,
                beat_in_bar=(index % meter_numerator) + 1,
                source=source_kind,
            )
        )

    payload = dict(metadata or {})
    payload.update(
        {
            "tempo_range_bpm": [round(float(np.min(local_bpms)), 3), round(float(np.max(local_bpms)), 3)],
            "tempo_variability": round(variability, 6),
            "preserves_rubato": True,
            "abrupt_detector_jump_limit_fraction": 0.22,
        }
    )
    return VariableTempoMap(
        source_ref=source_ref,
        source_sha256=source_sha256,
        sample_rate=int(sample_rate),
        duration_seconds=round(float(duration_seconds), 6),
        working_bpm=round(working_bpm, 4),
        variable=is_variable,
        meter_numerator=meter_numerator,
        meter_denominator=meter_denominator,
        anchors=anchors,
        beat_times_seconds=[round(float(value), 6) for value in beats],
        metadata=payload,
    )


def analyse_audio_tempo_map(
    source: Path,
    *,
    source_ref: str,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    preferred_bpm: float | None = None,
) -> VariableTempoMap:
    source = source.resolve()
    info = sf.info(source)
    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("Smart Warp source audio is empty")
    duration = float(info.frames / info.samplerate)
    y, sr = librosa.load(source, sr=None, mono=True)
    if y.size == 0:
        raise ValueError("Smart Warp source audio is empty")
    hop = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

    if preferred_bpm is not None:
        bpm = float(np.clip(preferred_bpm, 30.0, 300.0))
        onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop, units="frames")
        onset_times = librosa.frames_to_time(np.asarray(onset_frames), sr=sr, hop_length=hop)
        start = float(onset_times[0]) if len(onset_times) and float(onset_times[0]) < (60.0 / bpm) else 0.0
        step = 60.0 / bpm
        beats = np.arange(start, duration + 1e-9, step, dtype=float)
        kind: Literal["beat", "onset", "fixed_grid"] = "fixed_grid"
        metadata = {"preferred_bpm": bpm, "beat_detection_fallback": False}
    else:
        tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop)
        beats = librosa.frames_to_time(np.asarray(beat_frames), sr=sr, hop_length=hop)
        kind = "beat"
        metadata = {"librosa_working_bpm": round(float(np.asarray(tempo).reshape(-1)[0]), 4) if np.asarray(tempo).size else None}
        if len(beats) < 4:
            onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop, units="frames")
            onset_times = librosa.frames_to_time(np.asarray(onset_frames), sr=sr, hop_length=hop)
            beats = onset_times
            kind = "onset"
            metadata["beat_detection_fallback"] = True
        else:
            metadata["beat_detection_fallback"] = False

    return tempo_map_from_beats(
        beats,
        source_ref=source_ref,
        source_sha256=_sha256(source),
        sample_rate=int(sr),
        duration_seconds=duration,
        meter_numerator=meter_numerator,
        meter_denominator=meter_denominator,
        source_kind=kind,
        metadata=metadata,
    )


def persist_tempo_map(project: Path, tempo_map: VariableTempoMap, *, key: str) -> str:
    safe_key = "".join(ch for ch in key if ch.isalnum() or ch in {"-", "_"})[:160]
    if not safe_key:
        raise ValueError("Tempo map key is invalid")
    path = project.resolve() / "work" / "tempo_maps" / f"{safe_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tempo_map.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return _relative(project, path)


def load_tempo_map(project: Path, ref: str) -> VariableTempoMap:
    path = _safe_project_file(project, ref)
    return VariableTempoMap.model_validate_json(path.read_text(encoding="utf-8"))


def tempo_map_from_performance_input(project: Path, performance_input) -> tuple[VariableTempoMap, str]:
    source = _safe_project_file(project, performance_input.source_ref)
    beat_times = list(performance_input.beat_times_seconds or [])
    if len(beat_times) >= 4:
        info = sf.info(source)
        tempo_map = tempo_map_from_beats(
            beat_times,
            source_ref=performance_input.source_ref,
            source_sha256=_sha256(source),
            sample_rate=int(info.samplerate),
            duration_seconds=float(info.frames / info.samplerate),
            metadata={
                "performance_input_id": performance_input.id,
                "performance_input_kind": performance_input.kind,
                "analysis_reused": True,
            },
        )
    else:
        tempo_map = analyse_audio_tempo_map(
            source,
            source_ref=performance_input.source_ref,
        )
        tempo_map.metadata.update(
            {
                "performance_input_id": performance_input.id,
                "performance_input_kind": performance_input.kind,
                "analysis_reused": False,
            }
        )
    ref = persist_tempo_map(project, tempo_map, key=performance_input.id)
    return tempo_map, ref


def _fit_length(segment: np.ndarray, frames: int) -> np.ndarray:
    frames = max(1, int(frames))
    if segment.shape[0] > frames:
        return segment[:frames]
    if segment.shape[0] < frames:
        pad = np.zeros((frames - segment.shape[0], segment.shape[1]), dtype=segment.dtype)
        return np.concatenate([segment, pad], axis=0)
    return segment


def _time_stretch_segment(segment: np.ndarray, *, rate: float, target_frames: int) -> np.ndarray:
    channels = []
    for channel in range(segment.shape[1]):
        rendered = librosa.effects.time_stretch(np.asarray(segment[:, channel], dtype=np.float32), rate=float(rate))
        channels.append(rendered.astype(np.float32, copy=False))
    common = max(1, min(len(channel) for channel in channels))
    stacked = np.stack([channel[:common] for channel in channels], axis=1)
    return _fit_length(stacked, target_frames)


def _crossfade_append(parts: list[np.ndarray], segment: np.ndarray, *, crossfade_frames: int) -> None:
    if not parts or crossfade_frames <= 0:
        parts.append(segment)
        return
    previous = parts[-1]
    overlap = min(crossfade_frames, previous.shape[0] // 4, segment.shape[0] // 4)
    if overlap <= 1:
        parts.append(segment)
        return
    fade = np.linspace(0.0, 1.0, overlap, dtype=np.float32)[:, None]
    mixed = previous[-overlap:] * (1.0 - fade) + segment[:overlap] * fade
    parts[-1] = np.concatenate([previous[:-overlap], mixed], axis=0)
    parts.append(segment[overlap:])


def conform_audio_to_tempo_map(
    source: Path,
    output: Path,
    target_map: VariableTempoMap,
    *,
    source_ref: str,
    source_bpm: float | None = None,
    max_stretch_ratio: float = 1.8,
    crossfade_ms: float = 4.0,
) -> tuple[Path, TempoConformReport]:
    if not 1.05 <= max_stretch_ratio <= 3.0:
        raise ValueError("max_stretch_ratio must be between 1.05 and 3.0")
    source = source.resolve()
    before = _sha256(source)
    data, sr = sf.read(source, always_2d=True, dtype="float32")
    if data.shape[0] < 512:
        raise ValueError("Smart Warp source audio is too short")
    source_duration = float(data.shape[0] / sr)
    source_map = analyse_audio_tempo_map(source, source_ref=source_ref, preferred_bpm=source_bpm)

    source_beats = _clean_beat_times(source_map.beat_times_seconds, duration=source_duration)
    target_beats = _clean_beat_times(target_map.beat_times_seconds, duration=target_map.duration_seconds)
    usable = min(len(source_beats), len(target_beats))
    if usable < 4:
        raise ValueError("Smart Warp needs at least four corresponding source and target beats")
    # A very different beat count normally means the wrong source/guide was selected. Failing
    # closed is safer than stretching an unrelated recording into an apparently valid result.
    count_ratio = max(len(source_beats), len(target_beats)) / max(1, min(len(source_beats), len(target_beats)))
    if count_ratio > 1.35:
        raise ValueError("Source and target beat counts are too different for automatic Smart Warp")

    src = source_beats[:usable]
    tgt = target_beats[:usable]
    source_anchors = [0.0]
    target_anchors = [0.0]
    for source_time, target_time in zip(src, tgt):
        if source_time <= source_anchors[-1] + 0.04 or target_time <= target_anchors[-1] + 0.04:
            continue
        source_anchors.append(float(source_time))
        target_anchors.append(float(target_time))
    if source_duration > source_anchors[-1] + 0.04 and target_map.duration_seconds > target_anchors[-1] + 0.04:
        source_anchors.append(source_duration)
        target_anchors.append(float(target_map.duration_seconds))
    if len(source_anchors) < 5:
        raise ValueError("Smart Warp could not build a stable source-to-performance alignment")

    scales: list[float] = []
    parts: list[np.ndarray] = []
    crossfade_frames = max(0, int(sr * max(0.0, min(20.0, crossfade_ms)) / 1000.0))
    for index in range(len(source_anchors) - 1):
        source_start, source_end = source_anchors[index], source_anchors[index + 1]
        target_start, target_end = target_anchors[index], target_anchors[index + 1]
        source_span = source_end - source_start
        target_span = target_end - target_start
        if source_span <= 0.01 or target_span <= 0.01:
            continue
        duration_scale = target_span / source_span
        if duration_scale < 1.0 / max_stretch_ratio or duration_scale > max_stretch_ratio:
            raise ValueError(
                f"Smart Warp segment {index + 1} requires unsafe {duration_scale:.3f}x duration scaling"
            )
        start_frame = max(0, min(data.shape[0], round(source_start * sr)))
        end_frame = max(start_frame + 1, min(data.shape[0], round(source_end * sr)))
        segment = data[start_frame:end_frame]
        target_frames = max(1, round(target_span * sr))
        # librosa's phase-vocoder stretch rate is inverse to desired duration scale.
        rendered = _time_stretch_segment(segment, rate=1.0 / duration_scale, target_frames=target_frames)
        _crossfade_append(parts, rendered, crossfade_frames=crossfade_frames)
        scales.append(float(duration_scale))

    if not parts or not scales:
        raise ValueError("Smart Warp produced no renderable timing segments")
    output_audio = np.concatenate(parts, axis=0)
    expected_frames = max(1, round(float(target_map.duration_seconds) * sr))
    output_audio = _fit_length(output_audio, expected_frames)
    peak = float(np.max(np.abs(output_audio))) if output_audio.size else 0.0
    if peak > 1.0:
        output_audio = output_audio / peak
        peak = 1.0
    rms = float(np.sqrt(np.mean(np.square(output_audio, dtype=np.float64)))) if output_audio.size else 0.0
    if not np.isfinite(rms) or rms <= 1e-8:
        raise ValueError("Smart Warp output is silent or invalid")

    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, output_audio, sr, subtype="PCM_24")
    after = _sha256(source)
    if before != after:
        output.unlink(missing_ok=True)
        raise RuntimeError("Smart Warp source integrity check failed")

    report = TempoConformReport(
        source_sha256_before=before,
        source_sha256_after=after,
        target_tempo_map_id=target_map.id,
        source_duration_seconds=round(source_duration, 6),
        output_duration_seconds=round(float(len(output_audio) / sr), 6),
        sample_rate=int(sr),
        channels=int(output_audio.shape[1]),
        segment_count=len(scales),
        duration_scale_min=round(float(min(scales)), 6),
        duration_scale_max=round(float(max(scales)), 6),
        duration_scale_mean=round(float(np.mean(scales)), 6),
        output_peak=round(peak, 8),
        output_rms=round(rms, 8),
        source_beats_used=int(usable),
        target_beats_used=int(usable),
    )
    return output, report


__all__ = [
    "TEMPO_ENGINE_VERSION",
    "TempoAnchor",
    "VariableTempoMap",
    "TempoConformReport",
    "analyse_audio_tempo_map",
    "tempo_map_from_beats",
    "tempo_map_from_performance_input",
    "persist_tempo_map",
    "load_tempo_map",
    "conform_audio_to_tempo_map",
]
