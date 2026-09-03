from __future__ import annotations

import json
from pathlib import Path

import mido
import numpy as np
import pytest
import soundfile as sf

from aura_music_studio import transcription


def _write_phrase(path: Path, *, sr: int = 22050) -> None:
    duration = 1.6
    t = np.arange(int(sr * duration), dtype=np.float32) / sr
    signal = np.zeros_like(t)
    first = t < 0.75
    second = (t >= 0.85) & (t < 1.55)
    signal[first] = 0.16 * np.sin(2.0 * np.pi * 440.0 * t[first])
    signal[second] = 0.72 * np.sin(2.0 * np.pi * 523.251 * t[second])
    # Short fades reduce boundary clicks without removing the deliberate level contrast.
    fade = max(1, int(sr * 0.01))
    for edge in (0, int(sr * 0.75), int(sr * 0.85), int(sr * 1.55)):
        lo = max(0, edge - fade)
        hi = min(len(signal), edge + fade)
        if hi > lo:
            window = np.linspace(0.0, 1.0, hi - lo, dtype=np.float32)
            if edge in (int(sr * 0.75), int(sr * 1.55)):
                window = window[::-1]
            signal[lo:hi] *= window
    sf.write(path, signal, sr, subtype="PCM_24")


def _note_ons(path: Path) -> list[mido.Message]:
    midi = mido.MidiFile(path)
    return [
        message
        for message in mido.merge_tracks(midi.tracks)
        if message.type == "note_on" and int(message.velocity) > 0
    ]


def test_monophonic_transcription_preserves_phrase_pitch_and_source_dynamics(tmp_path):
    source = tmp_path / "phrase.wav"
    output = tmp_path / "phrase.mid"
    _write_phrase(source)

    transcription.audio_to_midi(
        source,
        output,
        mode="monophonic",
        bpm=120.0,
        min_note_ms=80.0,
        velocity_tracking=True,
    )

    notes = _note_ons(output)
    assert len(notes) >= 2
    pitches = [message.note for message in notes]
    assert any(abs(pitch - 69) <= 1 for pitch in pitches)
    assert any(abs(pitch - 72) <= 1 for pitch in pitches)
    velocities = [message.velocity for message in notes]
    assert max(velocities) - min(velocities) >= 15

    report_path = output.with_suffix(".mid.aura.json")
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["engine"] == "librosa_pyin"
    assert report["symbolic_guide_only"] is True
    assert report["final_audio"] is False
    assert report["source_sha256"]
    assert report["output_sha256"]
    assert report["note_count"] >= 2
    assert report["velocity_tracking"] is True
    assert transcription.transcription_metadata(output) == report


def test_transcription_rejects_silent_audio(tmp_path):
    source = tmp_path / "silent.wav"
    output = tmp_path / "silent.mid"
    sf.write(source, np.zeros(22050, dtype=np.float32), 22050)

    with pytest.raises(ValueError, match="silent"):
        transcription.audio_to_midi(source, output, mode="monophonic")
    assert not output.exists()


def test_explicit_polyphonic_mode_fails_closed_without_basic_pitch(tmp_path, monkeypatch):
    source = tmp_path / "phrase.wav"
    output = tmp_path / "phrase.mid"
    _write_phrase(source)

    monkeypatch.setattr(transcription.importlib.util, "find_spec", lambda name: None if name == "basic_pitch" else None)
    with pytest.raises(RuntimeError, match="Basic Pitch"):
        transcription.audio_to_midi(source, output, mode="polyphonic")
    assert not output.exists()


def test_transcription_contract_rejects_invalid_mode_and_output_extension(tmp_path):
    source = tmp_path / "phrase.wav"
    _write_phrase(source)

    with pytest.raises(ValueError, match="mode"):
        transcription.audio_to_midi(source, tmp_path / "bad.mid", mode="pretend-polyphonic")
    with pytest.raises(ValueError, match=".mid"):
        transcription.audio_to_midi(source, tmp_path / "bad.wav", mode="monophonic")
