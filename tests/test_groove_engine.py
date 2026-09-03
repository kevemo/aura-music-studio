from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.groove_engine import (
    conform_audio_to_groove,
    groove_template_from_times,
    load_groove_template,
    persist_groove_template,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_track(path: Path, *, duration: float = 4.0, sr: int = 22050, tone_hz: float = 330.0) -> None:
    frames = int(duration * sr)
    t = np.arange(frames, dtype=np.float32) / sr
    y = (0.16 * np.sin(2.0 * np.pi * tone_hz * t)).astype(np.float32)
    for sec in np.arange(0.0, duration, 0.25):
        start = int(sec * sr)
        length = min(128, frames - start)
        if length > 0:
            y[start : start + length] += np.hanning(length).astype(np.float32) * 0.45
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sr, subtype="PCM_24")


def _dominant_frequency(path: Path) -> float:
    y, sr = sf.read(path, dtype="float32")
    if y.ndim > 1:
        y = np.mean(y, axis=1)
    spectrum = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), 1.0 / sr)
    # Ignore the click/transient-heavy low-frequency bins.
    valid = freqs >= 80.0
    return float(freqs[valid][int(np.argmax(spectrum[valid]))])


def _moderate_swing_times(duration: float = 4.0) -> tuple[list[float], list[float]]:
    beats = [round(value, 6) for value in np.arange(0.0, duration, 0.5)]
    onsets: list[float] = []
    for beat in beats:
        if beat < duration:
            onsets.append(round(beat, 6))
        offbeat = beat + 0.275  # 55% eighth-note placement: audible pocket without unsafe warping.
        if offbeat < duration:
            onsets.append(round(offbeat, 6))
    return beats, onsets


def test_groove_template_detects_swing_and_persists_inside_project(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    source = project / "guide.wav"
    _write_track(source)
    beats, onsets = _moderate_swing_times()

    template = groove_template_from_times(
        beats,
        onsets,
        source_ref="guide.wav",
        source_sha256=_sha256(source),
        sample_rate=22050,
        duration_seconds=4.0,
        source_performance_input_id="guide_swing",
    )

    assert template.subdivisions_per_beat == 4
    assert 0.54 <= template.swing_ratio <= 0.56
    assert template.metadata["groove_is_non_destructive"] is True
    assert any(slot.observations for slot in template.slots)
    assert any(abs(slot.offset_fraction_of_beat) > 0.02 for slot in template.slots)

    ref = persist_groove_template(project, template, key="guide_swing")
    assert ref == "work/groove_templates/guide_swing.json"
    assert load_groove_template(project, ref).id == template.id

    with pytest.raises(ValueError, match="escaped"):
        load_groove_template(project, "../outside.json")


def test_groove_render_is_real_pitch_preserving_deterministic_and_source_safe(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    source = project / "source.wav"
    output_a = project / "output" / "groove_a.wav"
    output_b = project / "output" / "groove_b.wav"
    _write_track(source, tone_hz=330.0)
    beats, onsets = _moderate_swing_times()
    template = groove_template_from_times(
        beats,
        onsets,
        source_ref="guide.wav",
        source_sha256="0" * 64,
        sample_rate=22050,
        duration_seconds=4.0,
    )
    before = _sha256(source)

    _, report_a = conform_audio_to_groove(
        source,
        output_a,
        template,
        source_ref="source.wav",
        instrument_role="drums",
        source_bpm=120.0,
        groove_strength=1.0,
        humanize_timing_ms=4.0,
        humanize_seed=77,
        max_shift_ms=80.0,
        max_stretch_ratio=1.35,
    )
    _, report_b = conform_audio_to_groove(
        source,
        output_b,
        template,
        source_ref="source.wav",
        instrument_role="drums",
        source_bpm=120.0,
        groove_strength=1.0,
        humanize_timing_ms=4.0,
        humanize_seed=77,
        max_shift_ms=80.0,
        max_stretch_ratio=1.35,
    )

    assert _sha256(source) == before
    assert report_a.source_sha256_before == report_a.source_sha256_after == before
    assert report_a.real_audio is True
    assert report_a.pitch_preserving_algorithm is True
    assert report_a.destructive_source_edit is False
    assert report_a.max_applied_shift_ms <= 80.0
    assert report_a.max_applied_shift_ms > 0.0
    assert report_a.humanize_seed == 77
    assert report_a.humanize_timing_ms == 4.0
    assert abs(report_a.output_duration_seconds - 4.0) < 0.02

    rendered_a, sr_a = sf.read(output_a, dtype="float32")
    rendered_b, sr_b = sf.read(output_b, dtype="float32")
    assert sr_a == sr_b == 22050
    assert rendered_a.shape == rendered_b.shape
    assert np.allclose(rendered_a, rendered_b, atol=2e-5)
    assert not np.allclose(rendered_a, sf.read(source, dtype="float32")[0], atol=2e-5)
    assert abs(_dominant_frequency(output_a) - _dominant_frequency(source)) < 3.0
    assert report_a.model_dump() == report_b.model_dump(exclude={"source_sha256_before", "source_sha256_after"}) | {
        "source_sha256_before": report_b.source_sha256_before,
        "source_sha256_after": report_b.source_sha256_after,
    }


def test_groove_render_rejects_unsupported_role_and_extreme_contract_values(tmp_path):
    source = tmp_path / "source.wav"
    _write_track(source)
    beats, onsets = _moderate_swing_times()
    template = groove_template_from_times(
        beats,
        onsets,
        source_ref="guide.wav",
        source_sha256="0" * 64,
        sample_rate=22050,
        duration_seconds=4.0,
    )

    with pytest.raises(ValueError, match="instrument role"):
        conform_audio_to_groove(source, tmp_path / "bad.wav", template, source_ref="source.wav", instrument_role="vocals")
    with pytest.raises(ValueError, match="humanize_timing_ms"):
        conform_audio_to_groove(
            source,
            tmp_path / "bad2.wav",
            template,
            source_ref="source.wav",
            humanize_timing_ms=80.0,
        )
