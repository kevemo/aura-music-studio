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

from .tempo_engine import analyse_audio_tempo_map


GROOVE_ENGINE_VERSION = "aura-groove-humanisation-v1"
SUPPORTED_INSTRUMENT_ROLES = {"drums", "bass", "guitar", "piano", "percussion", "other"}


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
        raise ValueError("Groove source escaped the member project") from exc
    if not target.is_file():
        raise FileNotFoundError(ref)
    return target


def _relative(project: Path, path: Path) -> str:
    root = project.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Groove artifact escaped the member project") from exc


class GrooveSlot(BaseModel):
    slot_index: int = Field(ge=0, le=63)
    beat_in_bar: int = Field(ge=1, le=32)
    subdivision_index: int = Field(ge=0, le=15)
    offset_fraction_of_beat: float = Field(ge=-0.24, le=0.24)
    median_offset_ms: float = Field(ge=-500.0, le=500.0)
    observations: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)


class GrooveTemplate(BaseModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: f"groove_{uuid4().hex}")
    source_ref: str
    source_sha256: str
    source_performance_input_id: str | None = None
    sample_rate: int = Field(gt=0)
    duration_seconds: float = Field(gt=0.0)
    working_bpm: float = Field(ge=20.0, le=400.0)
    meter_numerator: int = Field(default=4, ge=1, le=16)
    meter_denominator: int = Field(default=4, ge=1, le=16)
    subdivisions_per_beat: Literal[2, 4] = 4
    beat_times_seconds: list[float] = Field(min_length=4)
    onset_times_seconds: list[float] = Field(min_length=4)
    slots: list[GrooveSlot] = Field(min_length=1)
    swing_ratio: float = Field(ge=0.5, le=0.75)
    timing_variation_ms: float = Field(ge=0.0, le=500.0)
    analysis_engine: str = GROOVE_ENGINE_VERSION
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class GrooveConformReport(BaseModel):
    engine: str = GROOVE_ENGINE_VERSION
    real_audio: bool = True
    pitch_preserving_algorithm: bool = True
    destructive_source_edit: bool = False
    source_sha256_before: str
    source_sha256_after: str
    groove_template_id: str
    instrument_role: str
    source_duration_seconds: float
    output_duration_seconds: float
    sample_rate: int
    channels: int
    timing_anchor_count: int
    rendered_segment_count: int
    groove_strength: float
    humanize_timing_ms: float
    humanize_seed: int
    max_applied_shift_ms: float
    duration_scale_min: float
    duration_scale_max: float
    output_peak: float
    output_rms: float


def _clean_times(values: list[float] | np.ndarray, *, duration: float, min_gap: float = 0.025) -> np.ndarray:
    rows = sorted({round(float(value), 6) for value in values if np.isfinite(value)})
    clean: list[float] = []
    for value in rows:
        if value < 0.0 or value > duration + 0.05:
            continue
        value = max(0.0, min(duration, value))
        if clean and value - clean[-1] < min_gap:
            continue
        clean.append(value)
    return np.asarray(clean, dtype=float)


def _extract_onsets(source: Path) -> tuple[list[float], int, float]:
    info = sf.info(source)
    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("Groove source audio is empty")
    duration = float(info.frames / info.samplerate)
    y, sr = librosa.load(source, sr=None, mono=True)
    if y.size < 512:
        raise ValueError("Groove source audio is too short")
    hop = 256
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop,
        units="frames",
        backtrack=False,
    )
    times = librosa.frames_to_time(np.asarray(frames), sr=sr, hop_length=hop)
    return [round(float(value), 6) for value in times], int(sr), duration


def _slot_observations(
    beats: np.ndarray,
    onsets: np.ndarray,
    *,
    meter_numerator: int,
    subdivisions_per_beat: int,
) -> tuple[dict[int, list[tuple[float, float]]], list[float]]:
    observations: dict[int, list[tuple[float, float]]] = {}
    all_offsets_ms: list[float] = []
    if len(beats) < 4:
        return observations, all_offsets_ms
    cycle_slots = meter_numerator * subdivisions_per_beat
    for onset in onsets:
        beat_index = int(np.searchsorted(beats, onset, side="right") - 1)
        if beat_index < 0 or beat_index >= len(beats) - 1:
            continue
        start = float(beats[beat_index])
        end = float(beats[beat_index + 1])
        beat_span = end - start
        if beat_span < 0.15 or beat_span > 3.0:
            continue
        phase = (float(onset) - start) / beat_span
        subdivision_index = int(round(phase * subdivisions_per_beat))
        if subdivision_index >= subdivisions_per_beat:
            beat_index += 1
            if beat_index >= len(beats) - 1:
                continue
            start = float(beats[beat_index])
            end = float(beats[beat_index + 1])
            beat_span = end - start
            subdivision_index = 0
        if subdivision_index < 0:
            continue
        ideal_phase = subdivision_index / subdivisions_per_beat
        grid_time = start + ideal_phase * beat_span
        offset_seconds = float(onset) - grid_time
        offset_fraction = offset_seconds / beat_span
        # Events further than roughly one quarter of a beat from their nearest subdivision are
        # not reliable groove evidence; treating them as feel would distort intentional notes.
        if abs(offset_fraction) > 0.24:
            continue
        slot_index = (beat_index * subdivisions_per_beat + subdivision_index) % cycle_slots
        observations.setdefault(slot_index, []).append((offset_fraction, offset_seconds * 1000.0))
        all_offsets_ms.append(offset_seconds * 1000.0)
    return observations, all_offsets_ms


def groove_template_from_times(
    beat_times_seconds: list[float] | np.ndarray,
    onset_times_seconds: list[float] | np.ndarray,
    *,
    source_ref: str,
    source_sha256: str,
    sample_rate: int,
    duration_seconds: float,
    source_performance_input_id: str | None = None,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    subdivisions_per_beat: Literal[2, 4] = 4,
    metadata: dict | None = None,
) -> GrooveTemplate:
    beats = _clean_times(beat_times_seconds, duration=duration_seconds, min_gap=0.08)
    onsets = _clean_times(onset_times_seconds, duration=duration_seconds, min_gap=0.02)
    if len(beats) < 4:
        raise ValueError("Groove extraction requires at least four reliable beats")
    if len(onsets) < 4:
        raise ValueError("Groove extraction requires at least four reliable onsets")
    intervals = np.diff(beats)
    valid_intervals = intervals[(intervals >= 0.15) & (intervals <= 3.0)]
    if len(valid_intervals) < 3:
        raise ValueError("Groove extraction could not derive a stable beat grid")
    working_bpm = float(np.median(60.0 / valid_intervals))
    observations, all_offsets_ms = _slot_observations(
        beats,
        onsets,
        meter_numerator=meter_numerator,
        subdivisions_per_beat=subdivisions_per_beat,
    )
    if sum(len(rows) for rows in observations.values()) < 4:
        raise ValueError("Groove extraction found too few subdivision-aligned performance events")

    cycle_slots = meter_numerator * subdivisions_per_beat
    slots: list[GrooveSlot] = []
    for slot_index in range(cycle_slots):
        rows = observations.get(slot_index, [])
        if rows:
            fractions = np.asarray([row[0] for row in rows], dtype=float)
            offsets_ms = np.asarray([row[1] for row in rows], dtype=float)
            fraction = float(np.clip(np.median(fractions), -0.24, 0.24))
            median_ms = float(np.median(offsets_ms))
            spread = float(np.std(fractions)) if len(fractions) > 1 else 0.0
            count_factor = min(1.0, len(rows) / 4.0)
            confidence = float(np.clip(count_factor * (1.0 - min(1.0, spread / 0.12)), 0.15, 1.0))
        else:
            fraction = 0.0
            median_ms = 0.0
            confidence = 0.0
        beat_in_bar = (slot_index // subdivisions_per_beat) + 1
        subdivision_index = slot_index % subdivisions_per_beat
        slots.append(
            GrooveSlot(
                slot_index=slot_index,
                beat_in_bar=beat_in_bar,
                subdivision_index=subdivision_index,
                offset_fraction_of_beat=round(fraction, 6),
                median_offset_ms=round(median_ms, 4),
                observations=len(rows),
                confidence=round(confidence, 4),
            )
        )

    # For a sixteenth-note analysis, subdivision 2 is the off-beat eighth. Its actual phase is
    # 0.5 + the measured fractional delay. Straight feel is 0.5; classic triplet swing is ~0.667.
    offbeat_fractions: list[float] = []
    offbeat_index = subdivisions_per_beat // 2
    for slot in slots:
        if slot.subdivision_index == offbeat_index and slot.observations:
            offbeat_fractions.extend(
                0.5 + row[0]
                for row in observations.get(slot.slot_index, [])
            )
    swing_ratio = float(np.clip(np.median(offbeat_fractions), 0.5, 0.75)) if offbeat_fractions else 0.5
    timing_variation_ms = float(np.std(np.asarray(all_offsets_ms, dtype=float))) if all_offsets_ms else 0.0
    payload = dict(metadata or {})
    payload.update(
        {
            "cycle_bars": 1,
            "cycle_slots": cycle_slots,
            "groove_is_non_destructive": True,
            "final_audio_requires_real_audio_render": True,
            "observed_event_count": sum(len(rows) for rows in observations.values()),
        }
    )
    return GrooveTemplate(
        source_ref=source_ref,
        source_sha256=source_sha256,
        source_performance_input_id=source_performance_input_id,
        sample_rate=int(sample_rate),
        duration_seconds=round(float(duration_seconds), 6),
        working_bpm=round(working_bpm, 4),
        meter_numerator=meter_numerator,
        meter_denominator=meter_denominator,
        subdivisions_per_beat=subdivisions_per_beat,
        beat_times_seconds=[round(float(value), 6) for value in beats],
        onset_times_seconds=[round(float(value), 6) for value in onsets],
        slots=slots,
        swing_ratio=round(swing_ratio, 6),
        timing_variation_ms=round(timing_variation_ms, 4),
        metadata=payload,
    )


def groove_template_from_performance_input(project: Path, performance_input) -> tuple[GrooveTemplate, str]:
    source = _safe_project_file(project, performance_input.source_ref)
    info = sf.info(source)
    duration = float(info.frames / info.samplerate)
    beat_times = list(performance_input.beat_times_seconds or [])
    onset_times = list(performance_input.onset_times_seconds or [])
    if len(beat_times) < 4:
        tempo_map = analyse_audio_tempo_map(source, source_ref=performance_input.source_ref)
        beat_times = tempo_map.beat_times_seconds
    if len(onset_times) < 4:
        onset_times, _, _ = _extract_onsets(source)
    template = groove_template_from_times(
        beat_times,
        onset_times,
        source_ref=performance_input.source_ref,
        source_sha256=_sha256(source),
        sample_rate=int(info.samplerate),
        duration_seconds=duration,
        source_performance_input_id=performance_input.id,
        metadata={
            "performance_input_kind": performance_input.kind,
            "rights_record_id": performance_input.metadata.get("rights_record_id"),
            "analysis_reused_beats": len(performance_input.beat_times_seconds or []) >= 4,
            "analysis_reused_onsets": len(performance_input.onset_times_seconds or []) >= 4,
        },
    )
    ref = persist_groove_template(project, template, key=performance_input.id)
    return template, ref


def persist_groove_template(project: Path, template: GrooveTemplate, *, key: str) -> str:
    safe_key = "".join(ch for ch in key if ch.isalnum() or ch in {"-", "_"})[:160]
    if not safe_key:
        raise ValueError("Groove template key is invalid")
    path = project.resolve() / "work" / "groove_templates" / f"{safe_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(template.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return _relative(project, path)


def load_groove_template(project: Path, ref: str) -> GrooveTemplate:
    path = _safe_project_file(project, ref)
    return GrooveTemplate.model_validate_json(path.read_text(encoding="utf-8"))


def _fit_length(segment: np.ndarray, frames: int) -> np.ndarray:
    frames = max(1, int(frames))
    if segment.shape[0] > frames:
        return segment[:frames]
    if segment.shape[0] < frames:
        pad = np.zeros((frames - segment.shape[0], segment.shape[1]), dtype=segment.dtype)
        return np.concatenate([segment, pad], axis=0)
    return segment


def _time_stretch_segment(segment: np.ndarray, *, rate: float, target_frames: int) -> np.ndarray:
    channels: list[np.ndarray] = []
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


def _subdivision_grid(beat_times: np.ndarray, *, subdivisions_per_beat: int, duration: float) -> list[tuple[float, int]]:
    rows: list[tuple[float, int]] = []
    for beat_index in range(len(beat_times) - 1):
        start = float(beat_times[beat_index])
        end = float(beat_times[beat_index + 1])
        span = end - start
        if span < 0.15 or span > 3.0:
            continue
        for sub_index in range(subdivisions_per_beat):
            value = start + span * sub_index / subdivisions_per_beat
            if value <= 0.015 or value >= duration - 0.015:
                continue
            rows.append((value, beat_index * subdivisions_per_beat + sub_index))
    return rows


def conform_audio_to_groove(
    source: Path,
    output: Path,
    template: GrooveTemplate,
    *,
    source_ref: str,
    instrument_role: str = "other",
    source_bpm: float | None = None,
    groove_strength: float = 1.0,
    humanize_timing_ms: float = 0.0,
    humanize_seed: int = 0,
    max_shift_ms: float = 100.0,
    max_stretch_ratio: float = 1.35,
    crossfade_ms: float = 3.0,
) -> tuple[Path, GrooveConformReport]:
    role = instrument_role.strip().lower()
    if role not in SUPPORTED_INSTRUMENT_ROLES:
        raise ValueError(f"Unsupported groove instrument role: {instrument_role}")
    if not 0.0 <= groove_strength <= 1.0:
        raise ValueError("groove_strength must be between 0 and 1")
    if not 0.0 <= humanize_timing_ms <= 40.0:
        raise ValueError("humanize_timing_ms must be between 0 and 40")
    if not 5.0 <= max_shift_ms <= 150.0:
        raise ValueError("max_shift_ms must be between 5 and 150")
    if not 1.05 <= max_stretch_ratio <= 1.8:
        raise ValueError("max_stretch_ratio must be between 1.05 and 1.8")

    source = source.resolve()
    before = _sha256(source)
    data, sr = sf.read(source, always_2d=True, dtype="float32")
    if data.shape[0] < 1024:
        raise ValueError("Groove source audio is too short")
    source_duration = float(data.shape[0] / sr)
    source_map = analyse_audio_tempo_map(source, source_ref=source_ref, preferred_bpm=source_bpm)
    beats = _clean_times(source_map.beat_times_seconds, duration=source_duration, min_gap=0.08)
    if len(beats) < 4:
        raise ValueError("Groove rendering needs at least four source beats")

    slots_by_index = {slot.slot_index: slot for slot in template.slots}
    cycle_slots = template.meter_numerator * template.subdivisions_per_beat
    grid = _subdivision_grid(beats, subdivisions_per_beat=template.subdivisions_per_beat, duration=source_duration)
    if len(grid) < 8:
        raise ValueError("Groove rendering could not build a stable subdivision grid")
    rng = np.random.default_rng(int(humanize_seed))
    source_anchors: list[float] = [0.0]
    target_anchors: list[float] = [0.0]
    shifts_ms: list[float] = [0.0]
    max_shift_seconds = max_shift_ms / 1000.0
    for source_time, global_slot in grid:
        slot = slots_by_index.get(global_slot % cycle_slots)
        groove_offset = 0.0
        if slot is not None and slot.observations:
            beat_index = global_slot // template.subdivisions_per_beat
            if beat_index >= len(beats) - 1:
                continue
            beat_span = float(beats[beat_index + 1] - beats[beat_index])
            groove_offset = slot.offset_fraction_of_beat * beat_span * groove_strength
        jitter = float(rng.uniform(-humanize_timing_ms, humanize_timing_ms) / 1000.0) if humanize_timing_ms else 0.0
        shift = float(np.clip(groove_offset + jitter, -max_shift_seconds, max_shift_seconds))
        target_time = float(np.clip(source_time + shift, 0.0, source_duration))
        if source_time <= source_anchors[-1] + 0.015:
            continue
        if target_time <= target_anchors[-1] + 0.015:
            # Do not allow an extreme groove or random imperfection to reverse musical time.
            target_time = target_anchors[-1] + 0.015
        if target_time >= source_duration - 0.015:
            continue
        source_anchors.append(float(source_time))
        target_anchors.append(float(target_time))
        shifts_ms.append((target_time - source_time) * 1000.0)
    source_anchors.append(source_duration)
    target_anchors.append(source_duration)
    shifts_ms.append(0.0)
    if len(source_anchors) < 10:
        raise ValueError("Groove rendering produced too few safe timing anchors")

    parts: list[np.ndarray] = []
    scales: list[float] = []
    crossfade_frames = max(0, int(sr * max(0.0, min(15.0, crossfade_ms)) / 1000.0))
    for index in range(len(source_anchors) - 1):
        source_start, source_end = source_anchors[index], source_anchors[index + 1]
        target_start, target_end = target_anchors[index], target_anchors[index + 1]
        source_span = source_end - source_start
        target_span = target_end - target_start
        if source_span <= 0.008 or target_span <= 0.008:
            continue
        duration_scale = target_span / source_span
        if duration_scale < 1.0 / max_stretch_ratio or duration_scale > max_stretch_ratio:
            raise ValueError(
                f"Groove segment {index + 1} requires unsafe {duration_scale:.3f}x duration scaling"
            )
        start_frame = max(0, min(data.shape[0], round(source_start * sr)))
        end_frame = max(start_frame + 1, min(data.shape[0], round(source_end * sr)))
        segment = data[start_frame:end_frame]
        target_frames = max(1, round(target_span * sr))
        rendered = _time_stretch_segment(segment, rate=1.0 / duration_scale, target_frames=target_frames)
        _crossfade_append(parts, rendered, crossfade_frames=crossfade_frames)
        scales.append(float(duration_scale))

    if not parts or not scales:
        raise ValueError("Groove renderer produced no audio")
    output_audio = np.concatenate(parts, axis=0)
    output_audio = _fit_length(output_audio, data.shape[0])
    peak = float(np.max(np.abs(output_audio))) if output_audio.size else 0.0
    if peak > 1.0:
        output_audio = output_audio / peak
        peak = 1.0
    rms = float(np.sqrt(np.mean(np.square(output_audio, dtype=np.float64)))) if output_audio.size else 0.0
    if not np.isfinite(rms) or rms <= 1e-8:
        raise ValueError("Groove output is silent or invalid")

    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, output_audio, sr, subtype="PCM_24")
    after = _sha256(source)
    if before != after:
        output.unlink(missing_ok=True)
        raise RuntimeError("Groove source integrity check failed")
    report = GrooveConformReport(
        source_sha256_before=before,
        source_sha256_after=after,
        groove_template_id=template.id,
        instrument_role=role,
        source_duration_seconds=round(source_duration, 6),
        output_duration_seconds=round(float(len(output_audio) / sr), 6),
        sample_rate=int(sr),
        channels=int(output_audio.shape[1]),
        timing_anchor_count=len(source_anchors),
        rendered_segment_count=len(scales),
        groove_strength=round(float(groove_strength), 6),
        humanize_timing_ms=round(float(humanize_timing_ms), 4),
        humanize_seed=int(humanize_seed),
        max_applied_shift_ms=round(float(max(abs(value) for value in shifts_ms)), 4),
        duration_scale_min=round(float(min(scales)), 6),
        duration_scale_max=round(float(max(scales)), 6),
        output_peak=round(peak, 8),
        output_rms=round(rms, 8),
    )
    return output, report


__all__ = [
    "GROOVE_ENGINE_VERSION",
    "SUPPORTED_INSTRUMENT_ROLES",
    "GrooveSlot",
    "GrooveTemplate",
    "GrooveConformReport",
    "groove_template_from_times",
    "groove_template_from_performance_input",
    "persist_groove_template",
    "load_groove_template",
    "conform_audio_to_groove",
]
