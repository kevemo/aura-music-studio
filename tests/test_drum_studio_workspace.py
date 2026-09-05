from __future__ import annotations

from pathlib import Path

import mido
import pytest

from aura_music_studio.daw import load_session, save_session
from aura_music_studio.drum_studio import DrumHit, DrumLane, DrumPattern
from aura_music_studio.drum_studio_workspace import (
    insert_pattern_into_daw,
    list_patterns,
    load_pattern,
    save_pattern,
    starter_pattern,
)
from aura_music_studio.session import StudioSession


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "drum-project"
    project.mkdir()
    session = StudioSession(name="Drum Project", bpm=126.0)
    session.add_track("Master", "master")
    save_session(project, session)
    return project


def test_pattern_persistence_is_project_scoped_and_truthful(tmp_path):
    project = _project(tmp_path)
    pattern = DrumPattern(
        name="Pocket Beat",
        swing=0.61,
        lanes=[DrumLane(instrument="kick", hits=[DrumHit(step=0), DrumHit(step=8)])],
    )

    saved = save_pattern(project, pattern, pattern_id="pocket_beat")
    loaded, payload = load_pattern(project, "pocket_beat")

    assert saved["pattern_id"] == "pocket_beat"
    assert saved["symbolic_guide_only"] is True
    assert saved["final_audio"] is False
    assert saved["storage_path_exposed"] is False
    assert loaded.name == "Pocket Beat"
    assert loaded.swing == pytest.approx(0.61)
    assert payload["pattern"]["lanes"][0]["instrument"] == "kick"
    assert list_patterns(project)[0]["hit_count"] == 2


def test_pattern_id_rejects_path_escape(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError, match="pattern id"):
        save_pattern(project, DrumPattern(), pattern_id="../../outside")
    assert not (tmp_path / "outside.json").exists()


def test_starter_pattern_can_be_inserted_as_real_midi_control_clip(tmp_path):
    project = _project(tmp_path)
    stored = starter_pattern(project, bars=2, steps_per_bar=16, seed=44)
    result = insert_pattern_into_daw(project, stored["pattern_id"])

    assert result["symbolic_guide_only"] is True
    assert result["final_audio"] is False
    assert result["storage_path_exposed"] is False
    assert result["report"]["rendered_hits"] > 0
    assert result["clip"]["source_path_exposed"] is False
    assert "source" not in result["clip"]

    session = load_session(project)
    midi_tracks = [track for track in session.tracks if track.role == "midi"]
    assert len(midi_tracks) == 1
    clip = midi_tracks[0].clips[0]
    assert clip.metadata["drum_studio"] is True
    assert clip.metadata["drum_pattern_id"] == stored["pattern_id"]
    assert clip.metadata["symbolic_guide_only"] is True
    assert clip.metadata["final_audio"] is False

    midi_path = project / str(clip.source)
    assert midi_path.is_file()
    midi = mido.MidiFile(midi_path)
    note_ons = [msg for track in midi.tracks for msg in track if msg.type == "note_on" and msg.velocity > 0]
    assert note_ons
    assert {msg.channel for msg in note_ons} == {9}
    assert {msg.note for msg in note_ons}.issuperset({36, 38, 42})


def test_empty_probability_pattern_fails_closed_without_orphan_midi(tmp_path):
    project = _project(tmp_path)
    pattern = DrumPattern(
        name="Never Plays",
        seed=1,
        lanes=[DrumLane(instrument="snare", hits=[DrumHit(step=4, probability=0.0)])],
    )
    saved = save_pattern(project, pattern, pattern_id="never")

    with pytest.raises(ValueError, match="did not render"):
        insert_pattern_into_daw(project, saved["pattern_id"])

    midi_dir = project / "input" / "midi"
    assert not midi_dir.exists() or not list(midi_dir.glob("drum-never-*.mid"))
