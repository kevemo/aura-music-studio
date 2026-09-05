from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.performance_inputs import analyse_performance_input


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_phrase(path: Path, sr: int = 22050) -> None:
    first_t = np.arange(int(sr * 0.8), dtype=np.float32) / sr
    gap = np.zeros(int(sr * 0.12), dtype=np.float32)
    second_t = np.arange(int(sr * 0.9), dtype=np.float32) / sr
    first = 0.12 * np.sin(2.0 * np.pi * 440.0 * first_t)
    second = 0.42 * np.sin(2.0 * np.pi * 523.251 * second_t)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.concatenate([first, gap, second]).astype(np.float32), sr, subtype="PCM_16")


def test_melody_performance_input_persists_transcription_provenance(tmp_path):
    project = tmp_path / "project"
    source = project / "input" / "performance_guides" / "melody.wav"
    _write_phrase(source)
    source_hash = _sha256(source)

    item = analyse_performance_input(
        project,
        source_ref="input/performance_guides/melody.wav",
        kind="melody",
        label="Played melody",
        intent="Turn this melody into an editable arrangement guide.",
        input_id="guide_test_melody",
    )

    assert _sha256(source) == source_hash
    assert item.midi_ref == "input/performance_guides/transcriptions/guide_test_melody.mid"
    midi_path = project / item.midi_ref
    assert midi_path.is_file()
    assert item.metadata["midi_transcription_mode"] == "monophonic"
    assert item.metadata["midi_transcription_engine"] == "librosa_pyin"
    assert item.metadata["midi_output_sha256"] == _sha256(midi_path)
    assert item.metadata["midi_note_count"] >= 2
    assert item.metadata["midi_symbolic_guide_only"] is True
    assert item.metadata["midi_final_audio"] is False
    report = item.metadata["midi_transcription"]
    assert report["source_sha256"] == source_hash
    assert report["symbolic_guide_only"] is True
    assert report["final_audio"] is False


def test_instrument_performance_input_requests_auto_transcription_mode(tmp_path, monkeypatch):
    project = tmp_path / "project"
    source = project / "input" / "performance_guides" / "instrument.wav"
    _write_phrase(source)
    captured = {}

    def fake_audio_to_midi(source_path, output_midi, **kwargs):
        captured.update(kwargs)
        output_midi.parent.mkdir(parents=True, exist_ok=True)
        # Use a real MIDI output while this test verifies Performance Input routing semantics.
        from aura_music_studio.transcription import monophonic_audio_to_midi

        return monophonic_audio_to_midi(source_path, output_midi, bpm=float(kwargs["bpm"]))

    monkeypatch.setattr("aura_music_studio.performance_inputs.audio_to_midi", fake_audio_to_midi)

    item = analyse_performance_input(
        project,
        source_ref="input/performance_guides/instrument.wav",
        kind="instrument",
        label="Guitar idea",
        input_id="guide_test_instrument",
    )

    assert captured["mode"] == "auto"
    assert captured["bpm"] == pytest.approx(item.detected_bpm, abs=0.001)
    assert item.metadata["midi_symbolic_guide_only"] is True
    assert item.metadata["midi_final_audio"] is False
