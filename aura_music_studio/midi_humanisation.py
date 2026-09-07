from __future__ import annotations

import random

from pydantic import BaseModel, Field

from .daw_midi import MidiDocument

MIDI_HUMANISATION_VERSION = "aura-midi-humanisation-v1"


class MidiHumanizeRequest(BaseModel):
    """Bounded, deterministic performance variation for editable MIDI control data."""

    timing_ms: float = Field(default=12.0, ge=0.0, le=80.0)
    velocity_range: int = Field(default=6, ge=0, le=32)
    duration_percent: float = Field(default=4.0, ge=0.0, le=35.0)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    preserve_first_downbeat: bool = True


def humanize_midi_document(
    document: MidiDocument,
    body: MidiHumanizeRequest,
    *,
    bpm: float,
) -> MidiDocument:
    """Return a non-destructive, reproducible humanised MIDI document.

    Pitch, channel, CC and pitch-bend expression are preserved. Only note start timing,
    note-on velocity and note length receive bounded variation. The result remains symbolic
    MIDI control data and is never represented as final rendered audio.
    """
    working_bpm = float(bpm)
    if not 20.0 <= working_bpm <= 400.0:
        raise ValueError("MIDI humanisation BPM must be between 20 and 400")

    result = document.model_copy(deep=True)
    if not result.notes:
        return result

    rng = random.Random(int(body.seed))
    seconds_per_beat = 60.0 / working_bpm
    timing_beats = (float(body.timing_ms) / 1000.0) / seconds_per_beat
    duration_fraction = float(body.duration_percent) / 100.0
    minimum_duration = 1.0 / 480.0

    first_start = min(float(note.start_beat) for note in result.notes)
    for note in result.notes:
        original_start = float(note.start_beat)
        if body.preserve_first_downbeat and abs(original_start - first_start) <= 1e-9 and abs(first_start) <= 1e-9:
            timing_delta = 0.0
        else:
            timing_delta = rng.uniform(-timing_beats, timing_beats) if timing_beats else 0.0
        note.start_beat = max(0.0, round(original_start + timing_delta, 6))

        velocity_delta = rng.randint(-int(body.velocity_range), int(body.velocity_range)) if body.velocity_range else 0
        note.velocity = max(1, min(127, int(note.velocity) + velocity_delta))

        duration_scale = 1.0 + (rng.uniform(-duration_fraction, duration_fraction) if duration_fraction else 0.0)
        note.duration_beats = max(minimum_duration, round(float(note.duration_beats) * duration_scale, 6))

    result.notes.sort(key=lambda row: (row.start_beat, row.pitch, row.channel))
    return result


__all__ = ["MIDI_HUMANISATION_VERSION", "MidiHumanizeRequest", "humanize_midi_document"]
