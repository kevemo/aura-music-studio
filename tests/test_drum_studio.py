from __future__ import annotations

import pytest

from aura_music_studio.drum_studio import (
    GM_DRUM_CHANNEL,
    DrumHit,
    DrumLane,
    DrumPattern,
    four_on_the_floor,
    pattern_to_midi_document,
)


def test_four_on_floor_generates_editable_gm_drum_midi():
    pattern = four_on_the_floor(bars=2, steps_per_bar=16, seed=7)
    document, report = pattern_to_midi_document(pattern, bpm=120.0)

    assert document.notes
    assert {note.channel for note in document.notes} == {GM_DRUM_CHANNEL}
    assert {note.pitch for note in document.notes} == {36, 38, 42}
    assert report.bars == 2
    assert report.requested_hits == report.rendered_hits
    assert report.skipped_probability_hits == 0
    assert report.symbolic_guide_only is True
    assert report.final_audio is False


def test_swing_delays_odd_steps_without_moving_even_steps():
    straight = DrumPattern(
        steps_per_bar=16,
        swing=0.5,
        lanes=[DrumLane(instrument="closed_hat", hits=[DrumHit(step=0), DrumHit(step=1)])],
    )
    swung = straight.model_copy(deep=True)
    swung.swing = 0.66

    straight_doc, _ = pattern_to_midi_document(straight, bpm=120.0)
    swung_doc, _ = pattern_to_midi_document(swung, bpm=120.0)

    assert straight_doc.notes[0].start_beat == swung_doc.notes[0].start_beat == 0.0
    assert swung_doc.notes[1].start_beat > straight_doc.notes[1].start_beat


def test_seeded_humanisation_and_probability_are_deterministic():
    pattern = DrumPattern(
        bars=1,
        steps_per_bar=16,
        swing=0.58,
        humanize_timing_ms=12.0,
        humanize_velocity=7,
        seed=12345,
        lanes=[
            DrumLane(
                instrument="snare",
                hits=[
                    DrumHit(step=2, velocity=100, probability=0.65),
                    DrumHit(step=6, velocity=100, probability=0.65),
                    DrumHit(step=10, velocity=100, probability=0.65),
                    DrumHit(step=14, velocity=100, probability=0.65),
                ],
            )
        ],
    )

    first, first_report = pattern_to_midi_document(pattern, bpm=128.0)
    second, second_report = pattern_to_midi_document(pattern, bpm=128.0)

    assert first.model_dump() == second.model_dump()
    assert first_report == second_report
    assert all(1 <= note.velocity <= 127 for note in first.notes)
    assert first_report.requested_hits == 4
    assert first_report.rendered_hits + first_report.skipped_probability_hits == 4


def test_lane_can_use_custom_gm_compatible_note():
    pattern = DrumPattern(
        lanes=[DrumLane(instrument="custom_percussion", midi_note=75, hits=[DrumHit(step=0, velocity=96)])]
    )
    document, _ = pattern_to_midi_document(pattern)
    assert document.notes[0].pitch == 75
    assert document.notes[0].channel == 9


def test_pattern_rejects_unknown_lane_and_out_of_grid_hit():
    with pytest.raises(ValueError, match="Unknown drum instrument"):
        DrumLane(instrument="not-a-real-lane", hits=[DrumHit(step=0)])

    with pytest.raises(ValueError, match="exceeds the pattern grid"):
        DrumPattern(bars=1, steps_per_bar=16, lanes=[DrumLane(instrument="kick", hits=[DrumHit(step=16)])])


def test_pattern_rejects_invalid_grid_and_bpm():
    with pytest.raises(ValueError, match="steps_per_bar"):
        DrumPattern(steps_per_bar=12)

    pattern = four_on_the_floor()
    with pytest.raises(ValueError, match="bpm"):
        pattern_to_midi_document(pattern, bpm=10.0)
