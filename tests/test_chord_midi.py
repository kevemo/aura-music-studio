from __future__ import annotations

from aura_music_studio.chord_intelligence import ChordEvent
from aura_music_studio.chord_midi import chord_pitches, progression_to_midi_document, router


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


def test_chord_midi_route_is_registered():
    assert any(
        getattr(route, "path", None) == "/projects/{project_name}/song-dna/chords/midi-guide"
        and "POST" in (getattr(route, "methods", set()) or set())
        for route in router.routes
    )
