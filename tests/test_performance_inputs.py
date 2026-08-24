from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf
import yaml

from aura_music_studio.performance_inputs import (
    analyse_performance_input,
    apply_input_to_project,
    load_manifest,
    register_input,
    rhythm_to_midi,
)


def _write_rhythm(path: Path, sr: int = 22050) -> None:
    seconds = 4.0
    y = np.zeros(int(sr * seconds), dtype=np.float32)
    # Strong short impulses at a 120 BPM quarter-note pattern. The exact tempo estimator
    # is intentionally not asserted; the test verifies that timing is extracted and stored.
    for sec in np.arange(0.25, 3.76, 0.5):
        start = int(sec * sr)
        length = min(180, len(y) - start)
        if length > 0:
            y[start : start + length] += np.hanning(length).astype(np.float32) * 0.9
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sr)


def test_rhythm_to_midi_creates_symbolic_groove_guide(tmp_path):
    sr = 22050
    y = np.zeros(sr * 2, dtype=np.float32)
    onset_frames = np.array([10, 30, 50, 70], dtype=int)
    midi_path = rhythm_to_midi(y, sr, tmp_path / "groove.mid", bpm=120.0, onset_frames=onset_frames)

    assert midi_path.is_file()
    midi = mido.MidiFile(midi_path)
    note_ons = [msg for track in midi.tracks for msg in track if msg.type == "note_on" and msg.velocity > 0]
    assert len(note_ons) == 4
    assert all(msg.channel == 9 for msg in note_ons)


def test_rhythm_upload_becomes_editable_project_guide_not_final_audio(tmp_path):
    project = tmp_path / "song"
    source = project / "input" / "performance_guides" / "rhythm.wav"
    _write_rhythm(source)
    (project / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "project_name": "song",
                "title": "Song",
                "tempo_bpm": None,
                "prompt": "professional studio song",
                "project_dna": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    item = analyse_performance_input(
        project,
        source_ref="input/performance_guides/rhythm.wav",
        kind="rhythm",
        label="hand tapped groove",
        intent="Build realistic drums and instruments around my exact groove",
        input_id="guide_test",
    )
    register_input(project, item)

    assert item.source_ref.endswith("rhythm.wav")
    assert item.detected_bpm is not None
    assert item.metadata["symbolic_is_guide_only"] is True
    assert item.midi_ref and (project / item.midi_ref).is_file()
    assert "rhythmic/groove anchor" in item.generation_context

    applied = apply_input_to_project(project, item.id)
    assert applied.status == "applied"
    stored = load_manifest(project)
    assert stored.inputs[0].status == "applied"

    manifest = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    guides = manifest["project_dna"]["performance_inputs"]
    assert guides[0]["id"] == "guide_test"
    assert guides[0]["source_ref"].endswith("rhythm.wav")
    assert guides[0]["midi_ref"].endswith(".mid")
    assert "Performance guide guide_test:" in manifest["prompt"]
    # The source real audio remains separately referenced; MIDI is only the editable guide.
    assert guides[0]["source_ref"] != guides[0]["midi_ref"]
