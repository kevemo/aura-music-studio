from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from .daw_midi import MidiDocument, MidiNote

DRUM_STUDIO_ENGINE_VERSION = "aura-drum-studio-v1"
GM_DRUM_CHANNEL = 9

GM_DRUM_NOTES: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "clap": 39,
    "closed_hat": 42,
    "pedal_hat": 44,
    "low_tom": 45,
    "open_hat": 46,
    "mid_tom": 47,
    "crash": 49,
    "high_tom": 50,
    "ride": 51,
    "tambourine": 54,
    "cowbell": 56,
    "shaker": 70,
}


class DrumHit(BaseModel):
    step: int = Field(ge=0, le=2047)
    velocity: int = Field(default=100, ge=1, le=127)
    probability: float = Field(default=1.0, ge=0.0, le=1.0)
    length_steps: float = Field(default=0.5, gt=0.0, le=8.0)
    micro_shift_ms: float = Field(default=0.0, ge=-80.0, le=80.0)


class DrumLane(BaseModel):
    instrument: str = Field(min_length=1, max_length=64)
    midi_note: int | None = Field(default=None, ge=0, le=127)
    hits: list[DrumHit] = Field(default_factory=list, max_length=4096)
    mute: bool = False

    @model_validator(mode="after")
    def resolve_note(self):
        if self.midi_note is None and self.instrument not in GM_DRUM_NOTES:
            raise ValueError(f"Unknown drum instrument: {self.instrument}")
        return self

    @property
    def resolved_note(self) -> int:
        return int(self.midi_note if self.midi_note is not None else GM_DRUM_NOTES[self.instrument])


class DrumPattern(BaseModel):
    name: str = Field(default="Aura Drum Pattern", min_length=1, max_length=160)
    bars: int = Field(default=1, ge=1, le=64)
    steps_per_bar: int = Field(default=16)
    swing: float = Field(default=0.5, ge=0.5, le=0.75)
    humanize_timing_ms: float = Field(default=0.0, ge=0.0, le=40.0)
    humanize_velocity: int = Field(default=0, ge=0, le=24)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    lanes: list[DrumLane] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_grid(self):
        if self.steps_per_bar not in {8, 16, 32}:
            raise ValueError("steps_per_bar must be 8, 16 or 32")
        maximum = self.bars * self.steps_per_bar
        for lane in self.lanes:
            seen: set[int] = set()
            for hit in lane.hits:
                if hit.step >= maximum:
                    raise ValueError(f"Drum hit step {hit.step} exceeds the pattern grid")
                if hit.step in seen:
                    raise ValueError(f"Duplicate hit at step {hit.step} in {lane.instrument}")
                seen.add(hit.step)
        return self


@dataclass(frozen=True)
class DrumRenderReport:
    engine_version: str
    name: str
    bars: int
    steps_per_bar: int
    swing: float
    seed: int
    requested_hits: int
    rendered_hits: int
    skipped_probability_hits: int
    humanize_timing_ms: float
    humanize_velocity: int
    midi_channel: int = GM_DRUM_CHANNEL
    symbolic_guide_only: bool = True
    final_audio: bool = False

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _stable_rng(seed: int, lane_index: int, step: int, instrument: str) -> random.Random:
    payload = f"{DRUM_STUDIO_ENGINE_VERSION}:{seed}:{lane_index}:{step}:{instrument}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return random.Random(value)


def _step_position_beats(step: int, *, steps_per_bar: int, swing: float) -> float:
    step_beats = 4.0 / float(steps_per_bar)
    position = float(step) * step_beats
    if step % 2 == 1 and swing > 0.5:
        position += (float(swing) - 0.5) * 2.0 * step_beats
    return position


def pattern_to_midi_document(pattern: DrumPattern, *, bpm: float = 120.0) -> tuple[MidiDocument, DrumRenderReport]:
    """Convert a bounded step pattern into editable GM-drum MIDI control data.

    Timing, swing and humanisation are encoded in MIDI note positions/velocities. This is a
    symbolic edit/control layer only; it is never represented as rendered or mastered audio.
    """
    if not 20.0 <= float(bpm) <= 400.0:
        raise ValueError("bpm must be between 20 and 400")

    step_beats = 4.0 / float(pattern.steps_per_bar)
    requested = 0
    rendered = 0
    skipped = 0
    notes: list[MidiNote] = []

    for lane_index, lane in enumerate(pattern.lanes):
        if lane.mute:
            continue
        for hit in sorted(lane.hits, key=lambda row: row.step):
            requested += 1
            rng = _stable_rng(pattern.seed, lane_index, hit.step, lane.instrument)
            if hit.probability < 1.0 and rng.random() > hit.probability:
                skipped += 1
                continue

            start = _step_position_beats(hit.step, steps_per_bar=pattern.steps_per_bar, swing=pattern.swing)
            shift_ms = float(hit.micro_shift_ms)
            if pattern.humanize_timing_ms > 0.0:
                shift_ms += rng.uniform(-pattern.humanize_timing_ms, pattern.humanize_timing_ms)
            start += shift_ms * float(bpm) / 60000.0
            start = max(0.0, round(start, 6))

            velocity = int(hit.velocity)
            if pattern.humanize_velocity:
                velocity += rng.randint(-pattern.humanize_velocity, pattern.humanize_velocity)
            velocity = max(1, min(127, velocity))

            duration = max(0.01, round(step_beats * float(hit.length_steps), 6))
            notes.append(
                MidiNote(
                    pitch=lane.resolved_note,
                    start_beat=start,
                    duration_beats=duration,
                    velocity=velocity,
                    channel=GM_DRUM_CHANNEL,
                )
            )
            rendered += 1

    notes.sort(key=lambda row: (row.start_beat, row.pitch, row.velocity))
    document = MidiDocument(notes=notes, name=pattern.name)
    report = DrumRenderReport(
        engine_version=DRUM_STUDIO_ENGINE_VERSION,
        name=pattern.name,
        bars=pattern.bars,
        steps_per_bar=pattern.steps_per_bar,
        swing=round(float(pattern.swing), 4),
        seed=pattern.seed,
        requested_hits=requested,
        rendered_hits=rendered,
        skipped_probability_hits=skipped,
        humanize_timing_ms=round(float(pattern.humanize_timing_ms), 3),
        humanize_velocity=int(pattern.humanize_velocity),
    )
    return document, report


def four_on_the_floor(*, bars: int = 1, steps_per_bar: int = 16, seed: int = 0) -> DrumPattern:
    """Create a useful editable starter groove without pretending to generate final audio."""
    if steps_per_bar not in {8, 16, 32}:
        raise ValueError("steps_per_bar must be 8, 16 or 32")
    if not 1 <= int(bars) <= 64:
        raise ValueError("bars must be between 1 and 64")
    total = int(bars) * int(steps_per_bar)
    quarter = steps_per_bar // 4
    eighth = steps_per_bar // 8
    kick = [DrumHit(step=step, velocity=112) for step in range(0, total, quarter)]
    snare = [DrumHit(step=bar * steps_per_bar + quarter, velocity=108) for bar in range(bars)]
    snare += [DrumHit(step=bar * steps_per_bar + quarter * 3, velocity=108) for bar in range(bars)]
    hats = [DrumHit(step=step, velocity=82 if (step // eighth) % 2 == 0 else 72) for step in range(0, total, eighth)]
    return DrumPattern(
        name="Aura Four on the Floor",
        bars=bars,
        steps_per_bar=steps_per_bar,
        seed=seed,
        lanes=[
            DrumLane(instrument="kick", hits=kick),
            DrumLane(instrument="snare", hits=snare),
            DrumLane(instrument="closed_hat", hits=hats),
        ],
    )


__all__ = [
    "DRUM_STUDIO_ENGINE_VERSION",
    "GM_DRUM_CHANNEL",
    "GM_DRUM_NOTES",
    "DrumHit",
    "DrumLane",
    "DrumPattern",
    "DrumRenderReport",
    "four_on_the_floor",
    "pattern_to_midi_document",
]
