from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio.chord_intelligence import ChordEvent
from aura_music_studio import chord_midi
from aura_music_studio.chord_midi import (
    ChordMidiRegenerateRequest,
    chord_pitches,
    progression_to_midi_document,
    router,
)
from aura_music_studio.session import Clip, StudioSession, Track


def test_chord_pitches_support_common_qualities_and_slash_bass():
    assert chord_pitches("Cmaj7", root_octave=4) == [60, 64, 67, 71]
    assert chord_pitches("Dm7", root_octave=4) == [62, 65, 69, 72]
    assert chord_pitches("Bdim7", root_octave=4) == [71, 74, 77, 80]
    assert chord_pitches("C/E", root_octave=4) == [52, 60, 64, 67]


def test_progression_to_midi_document_preserves_song_timing_in_beats():
    events = [
        ChordEvent(id="one", symbol="C", start_seconds=0, end_seconds=4),
        ChordEvent(id="two", symbol="G7", start_seconds=4, end_seconds=8),
    ]
    document = progression_to_midi_document(
        events,
        bpm=120,
        name="Guide",
        velocity=90,
        root_octave=4,
        channel=2,
    )
    first = [note for note in document.notes if note.start_beat == 0]
    second = [note for note in document.notes if note.start_beat == 8]
    assert len(first) == 3
    assert len(second) == 4
    assert all(note.duration_beats == 8 for note in document.notes)
    assert all(note.velocity == 90 for note in document.notes)
    assert all(note.channel == 2 for note in document.notes)


def test_regenerate_existing_chord_guide_keeps_clip_identity_and_new_source(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    clip = Clip(
        id="clip-one",
        name="Harmony Guide",
        kind="midi",
        source="input/midi/chord_guides/original.mid",
        duration=4.0,
        metadata={
            "chord_guide": True,
            "symbolic_guide_only": True,
            "song_dna_version": 3,
            "chord_midi_settings": {"velocity": 77, "root_octave": 3, "channel": 4},
        },
    )
    track = Track(id="track-one", name="Harmony", role="midi", clips=[clip])
    session = StudioSession(name="Song", bpm=120, tracks=[track])
    events = [
        ChordEvent(id="one", symbol="Cmaj7", start_seconds=0, end_seconds=2),
        ChordEvent(id="two", symbol="F", start_seconds=2, end_seconds=6),
    ]
    saved = []
    written = []

    monkeypatch.setattr(
        chord_midi,
        "_member",
        lambda _request: SimpleNamespace(plan=SimpleNamespace(has=lambda _capability: False)),
    )
    monkeypatch.setattr(chord_midi, "_project", lambda _name: project)
    monkeypatch.setattr(chord_midi, "load_session", lambda _project: session)
    monkeypatch.setattr(chord_midi, "_progression", lambda _project: (events, 4))
    monkeypatch.setattr(
        chord_midi,
        "create_revision",
        lambda *_args, **_kwargs: {"id": "revision-before-regenerate"},
    )
    monkeypatch.setattr(chord_midi, "write_midi_document", lambda path, document, **_kwargs: written.append((path, document)))
    monkeypatch.setattr(chord_midi, "save_session", lambda _project, value: saved.append(value))

    response = chord_midi.regenerate_chord_midi_guide(
        "project",
        "clip-one",
        ChordMidiRegenerateRequest(),
        SimpleNamespace(),
    )

    assert response["midi_regenerated"] is True
    assert response["audio_rendered"] is False
    assert response["clip_id"] == "clip-one"
    assert response["previous_source"] == "input/midi/chord_guides/original.mid"
    assert response["source"] != response["previous_source"]
    assert response["song_dna_version"] == 4
    assert response["regeneration_count"] == 1
    assert len(written) == 1
    assert saved == [session]
    assert clip.id == "clip-one"
    assert clip.duration == 6.0
    assert clip.metadata["song_dna_version"] == 4
    assert clip.metadata["regenerated_from_source"] == "input/midi/chord_guides/original.mid"
    assert clip.metadata["chord_midi_settings"] == {"velocity": 77, "root_octave": 3, "channel": 4}
    assert clip.metadata["revision_before_generation"] == "revision-before-regenerate"


def test_chord_midi_routes_are_registered():
    routes = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set()))
        for route in router.routes
    }
    assert any(
        path == "/projects/{project_name}/song-dna/chords/midi-guide" and "POST" in methods
        for path, methods in routes
    )
    assert any(
        path == "/projects/{project_name}/song-dna/chords/midi-guide/{clip_id}/regenerate" and "POST" in methods
        for path, methods in routes
    )
