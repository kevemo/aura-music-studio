from __future__ import annotations

import hashlib
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from aura_music_studio.performance_inputs import analyse_performance_input
from aura_music_studio.transcription import audio_to_midi, transcription_metadata


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_monophonic_phrase(path: Path, sr: int = 22050) -> None:
    # Two clear notes with different dynamics exercise pitch, duration and velocity inference.
    first_t = np.arange(int(sr * 0.8), dtype=np.float32) / sr
    gap = np.zeros(int(sr * 0.12), dtype=np.float32)
    second_t = np.arange(int(sr * 0.9), dtype=np.float32) / sr
    first = 0.12 * np.sin(2.0 * np.pi * 440.0 * first_t)
    second = 0.42 * np.sin(2.0 * np.pi * 523.251 * second_t)
    audio = np.concatenate([first, gap, second]).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sr, subtype="PCM_16")


def test_monophonic_audio_to_midi_writes_truthful_provenance_and_preserves_source(tmp_path):
    source = tmp_path / "phrase.wav"
    output = tmp_path / "guide.mid"
    _write_monophonic_phrase(source)
    source_hash = _sha256(source)

    audio_to_midi(source, output, bpm=96.0, mode="monophonic", min_note_ms=80.0)
    report = transcription_metadata(output)
    midi = mido.MidiFile(output)
    note_ons = [
        message
        for message in mido.merge_tracks(midi.tracks)
        if message.type == "note_on" and int(message.velocity) > 0
    ]

    assert output.is_file()
    assert _sha256(source) == source_hash
    assert report["source_sha256"] == source_hash
    assert report["output_sha256"] == _sha256(output)
    assert report["engine"] == "librosa_pyin"
    assert report["mode_requested"] == "monophonic"
    assert report["polyphonic_capable"] is False
    assert report["symbolic_guide_only"] is True
    assert report["final_audio"] is False
    assert report["note_count"] >= 2
    assert len(note_ons) >= 2
    assert max(message.velocity for message in note_ons) > min(message.velocity for message in note_ons)
    pitches = [message.note for message in note_ons]
    assert any(abs(pitch - 69) <= 1 for pitch in pitches)
    assert any(abs(pitch - 72) <= 1 for pitch in pitches)


def test_performance_input_persists_midi_transcription_provenance(tmp_path):
    project = tmp_path / "project"
    source = project / "input" / "performance_guides" / "melody.wav"
    _write_monophonic_phrase(source)

    item = analyse_performance_input(
        project,
        source_ref="input/performance_guides/melody.wav",
        kind="melody",
        label="Played melody",
        intent="Turn this melody into an editable arrangement guide.",
        input_id="guide_test_melody",
    )

    assert item.midi_ref == "input/performance_guides/transcriptions/guide_test_melody.mid"
    assert (project / item.midi_ref).is_file()
    assert item.metadata["midi_transcription_mode"] == "monophonic"
    assert item.metadata["midi_transcription_engine"] == "librosa_pyin"
    assert item.metadata["midi_note_count"] >= 2
    assert item.metadata["midi_output_sha256"] == _sha256(project / item.midi_ref)
    assert item.metadata["midi_symbolic_guide_only"] is True
    assert item.metadata["midi_final_audio"] is False
    report = item.metadata["midi_transcription"]
    assert report["source_sha256"] == _sha256(source)
    assert report["symbolic_guide_only"] is True
    assert report["final_audio"] is False
