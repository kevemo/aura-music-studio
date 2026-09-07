from __future__ import annotations

import pytest

from aura_music_studio.daw_midi import MidiCC, MidiDocument, MidiNote, MidiPitchBend
from aura_music_studio.midi_humanisation import MidiHumanizeRequest, humanize_midi_document


def _document() -> MidiDocument:
    return MidiDocument(
        name="Human Performance",
        notes=[
            MidiNote(pitch=60, start_beat=0.0, duration_beats=1.0, velocity=80, channel=0),
            MidiNote(pitch=64, start_beat=1.0, duration_beats=0.75, velocity=92, channel=1),
            MidiNote(pitch=67, start_beat=2.0, duration_beats=1.5, velocity=105, channel=0),
        ],
        cc=[MidiCC(beat=0.5, control=11, value=97, channel=1)],
        pitch_bend=[MidiPitchBend(beat=1.25, value=512, channel=0)],
    )


def test_midi_humanisation_is_deterministic_bounded_and_non_destructive():
    source = _document()
    body = MidiHumanizeRequest(
        timing_ms=20.0,
        velocity_range=8,
        duration_percent=10.0,
        seed=2468,
        preserve_first_downbeat=True,
    )

    first = humanize_midi_document(source, body, bpm=120.0)
    second = humanize_midi_document(source, body, bpm=120.0)

    assert first == second
    assert first != source
    assert source.notes[0].start_beat == 0.0
    assert source.notes[1].velocity == 92
    assert first.notes[0].start_beat == 0.0

    timing_bound_beats = (20.0 / 1000.0) / (60.0 / 120.0)
    source_by_pitch = {note.pitch: note for note in source.notes}
    human_by_pitch = {note.pitch: note for note in first.notes}
    for pitch, original in source_by_pitch.items():
        changed = human_by_pitch[pitch]
        assert changed.pitch == original.pitch
        assert changed.channel == original.channel
        assert abs(changed.start_beat - original.start_beat) <= timing_bound_beats + 1e-6
        assert abs(changed.velocity - original.velocity) <= 8
        assert original.duration_beats * 0.9 - 1e-6 <= changed.duration_beats <= original.duration_beats * 1.1 + 1e-6

    assert first.cc == source.cc
    assert first.pitch_bend == source.pitch_bend


def test_midi_humanisation_seed_changes_performance_variation():
    source = _document()
    a = humanize_midi_document(source, MidiHumanizeRequest(seed=1), bpm=96.0)
    b = humanize_midi_document(source, MidiHumanizeRequest(seed=2), bpm=96.0)
    assert a != b


def test_midi_humanisation_zero_amounts_are_identity_copy():
    source = _document()
    result = humanize_midi_document(
        source,
        MidiHumanizeRequest(timing_ms=0, velocity_range=0, duration_percent=0, seed=99),
        bpm=120.0,
    )
    assert result == source
    assert result is not source


def test_midi_humanisation_rejects_invalid_bpm():
    with pytest.raises(ValueError, match="BPM"):
        humanize_midi_document(_document(), MidiHumanizeRequest(), bpm=0)
