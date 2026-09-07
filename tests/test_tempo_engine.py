from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.tempo_engine import (
    conform_audio_to_tempo_map,
    load_tempo_map,
    persist_tempo_map,
    tempo_map_from_beats,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_tonal_click_track(path: Path, *, duration: float = 4.0, sr: int = 22050) -> None:
    frames = int(duration * sr)
    t = np.arange(frames, dtype=np.float32) / sr
    y = (0.16 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
    for sec in np.arange(0.0, duration, 0.5):
        start = int(sec * sr)
        length = min(150, frames - start)
        if length > 0:
            y[start : start + length] += np.hanning(length).astype(np.float32) * 0.42
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sr, subtype="PCM_24")


def _target_map(*, duration: float = 4.6):
    return tempo_map_from_beats(
        [0.0, 0.50, 1.00, 1.52, 2.08, 2.68, 3.32, 4.10],
        source_ref="input/performance_guides/live.wav",
        source_sha256="a" * 64,
        sample_rate=22050,
        duration_seconds=duration,
        metadata={"performance_input_id": "guide_live"},
    )


def test_variable_tempo_map_preserves_gradual_human_timing_and_round_trips(tmp_path):
    tempo_map = _target_map()

    assert tempo_map.variable is True
    assert len(tempo_map.anchors) == 8
    assert [anchor.time_seconds for anchor in tempo_map.anchors] == sorted(
        anchor.time_seconds for anchor in tempo_map.anchors
    )
    assert tempo_map.metadata["preserves_rubato"] is True
    low, high = tempo_map.metadata["tempo_range_bpm"]
    assert high - low >= 4.0
    assert all(0.0 <= anchor.confidence <= 1.0 for anchor in tempo_map.anchors)
    assert [anchor.beat_in_bar for anchor in tempo_map.anchors[:4]] == [1, 2, 3, 4]

    ref = persist_tempo_map(tmp_path, tempo_map, key="guide_live")
    assert ref == "work/tempo_maps/guide_live.json"
    restored = load_tempo_map(tmp_path, ref)
    assert restored.model_dump(mode="json") == tempo_map.model_dump(mode="json")


def test_smart_warp_renders_non_silent_pitch_preserving_real_audio_without_touching_source(tmp_path):
    source = tmp_path / "input" / "assets" / "accompaniment.wav"
    output = tmp_path / "output" / "tempo_follow" / "warped.wav"
    _write_tonal_click_track(source)
    before = _sha(source)

    rendered, report = conform_audio_to_tempo_map(
        source,
        output,
        _target_map(),
        source_ref="input/assets/accompaniment.wav",
        source_bpm=120.0,
        max_stretch_ratio=1.8,
        crossfade_ms=3.0,
    )

    assert rendered == output
    assert output.is_file()
    assert _sha(source) == before
    assert report.source_sha256_before == report.source_sha256_after == before
    assert report.real_audio is True
    assert report.pitch_preserving_algorithm is True
    assert report.destructive_source_edit is False
    assert report.segment_count >= 4
    assert report.duration_scale_max > report.duration_scale_min
    assert report.output_rms > 0.01

    audio, sr = sf.read(output, dtype="float32")
    assert sr == 22050
    assert abs((len(audio) / sr) - 4.6) < 0.02
    assert float(np.max(np.abs(audio))) > 0.05

    # The tonal carrier remains around concert A after time stretching. This is deliberately a
    # broad spectral assertion: Smart Warp preserves pitch, but it is not a pitch-correction tool.
    middle = np.asarray(audio[int(sr * 1.0) : int(sr * 3.5)], dtype=np.float32)
    spectrum = np.abs(np.fft.rfft(middle * np.hanning(len(middle))))
    frequencies = np.fft.rfftfreq(len(middle), d=1.0 / sr)
    dominant = float(frequencies[int(np.argmax(spectrum))])
    assert 430.0 <= dominant <= 450.0


def test_smart_warp_fails_closed_when_alignment_requires_unsafe_stretch(tmp_path):
    source = tmp_path / "source.wav"
    _write_tonal_click_track(source)
    target = tempo_map_from_beats(
        [0.0, 0.5, 1.0, 1.8, 2.7, 3.6, 4.5, 5.4],
        source_ref="guide.wav",
        source_sha256="b" * 64,
        sample_rate=22050,
        duration_seconds=6.0,
    )

    with pytest.raises(ValueError, match="unsafe"):
        conform_audio_to_tempo_map(
            source,
            tmp_path / "unsafe.wav",
            target,
            source_ref="source.wav",
            source_bpm=120.0,
            max_stretch_ratio=1.4,
        )
    assert not (tmp_path / "unsafe.wav").exists()


def test_tempo_map_rejects_timing_without_enough_reliable_anchors():
    with pytest.raises(ValueError, match="at least four"):
        tempo_map_from_beats(
            [0.0, 0.5, 1.0],
            source_ref="guide.wav",
            source_sha256="c" * 64,
            sample_rate=22050,
            duration_seconds=2.0,
        )
