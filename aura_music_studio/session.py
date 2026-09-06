from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


AutomationInterpolation = Literal["hold", "linear", "smooth"]


def normalize_automation_parameter(value: str) -> tuple[str, tuple[float, float] | None]:
    """Return the canonical automation path and safe renderer bounds.

    Historical volume/pan aliases remain valid. Scoped paths let one track own automation for
    clips, sends and effects without adding parallel automation stores that can drift apart.
    Unknown paths are preserved for forward-compatible control metadata but receive no implicit
    numeric clamp until a renderer explicitly supports them.
    """
    parameter = (value or "").strip().lower()
    if parameter in {"volume", "volume_db", "fader", "gain_db"}:
        return "volume_db", (-60.0, 18.0)
    if parameter in {"pan", "balance"}:
        return "pan", (-1.0, 1.0)

    parts = parameter.split(":")
    if len(parts) == 3 and parts[1]:
        scope, resource_id, field = parts
        if scope == "clip" and field in {"gain", "gain_db", "volume", "volume_db"}:
            return f"clip:{resource_id}:gain_db", (-60.0, 18.0)
        if scope == "send" and field in {"level", "level_db", "gain", "gain_db"}:
            return f"send:{resource_id}:level_db", (-60.0, 12.0)
        if scope in {"fx", "effect"} and field in {"mix", "wet", "wet_dry", "wetdry"}:
            return f"fx:{resource_id}:mix", (0.0, 1.0)
    return parameter, None


class AutomationPoint(BaseModel):
    time: float
    value: float


class AutomationLane(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    parameter: str
    points: list[AutomationPoint] = Field(default_factory=list)
    interpolation: AutomationInterpolation = "linear"

    @model_validator(mode="after")
    def normalize_lane(self):
        parameter, bounds = normalize_automation_parameter(self.parameter)

        by_time: dict[float, AutomationPoint] = {}
        for point in self.points:
            time = float(point.time)
            value = float(point.value)
            if not math.isfinite(time) or not math.isfinite(value):
                continue
            time = max(0.0, time)
            if bounds:
                value = max(bounds[0], min(bounds[1], value))
            by_time[time] = AutomationPoint(time=time, value=value)

        object.__setattr__(self, "parameter", parameter)
        object.__setattr__(self, "points", [by_time[key] for key in sorted(by_time)])
        return self


class Effect(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: Literal[
        "gain", "eq", "highpass", "lowpass", "bandpass", "notch", "low_shelf", "high_shelf",
        "compressor", "limiter", "gate", "expander", "deesser", "reverb", "delay", "distortion",
        "saturation", "exciter", "chorus", "flanger", "phaser", "tremolo", "pitch_shift", "doubler",
        "denoise", "declick", "declip", "convolution", "stereo_width", "custom_safe_chain"
    ]
    enabled: bool = True
    # Static wet/dry balance. A scoped ``fx:<id>:mix`` lane can automate this in real audio.
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    parameters: dict[str, float | str | bool] = Field(default_factory=dict)


class Send(BaseModel):
    """Parallel post-fader send from a source track to an auxiliary bus."""
    id: str = Field(default_factory=lambda: uuid4().hex)
    bus_track_id: str
    level_db: float = Field(default=-18.0, ge=-60.0, le=12.0)
    enabled: bool = True


class Clip(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    kind: Literal["audio", "midi", "lyrics", "marker"]
    source: str | None = None
    start: float = 0.0
    duration: float = 0.0
    source_offset: float = 0.0
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    muted: bool = False
    take_lane: int = 0
    metadata: dict = Field(default_factory=dict)


class Track(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    role: Literal[
        "master", "bus", "vocals", "backing_vocals", "drums", "bass", "guitar", "piano", "keyboard",
        "strings", "synth", "percussion", "brass", "woodwinds", "fx", "midi", "other"
    ] = "other"
    clips: list[Clip] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    automation: list[AutomationLane] = Field(default_factory=list)
    sends: list[Send] = Field(default_factory=list)
    volume_db: float = 0.0
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    mute: bool = False
    solo: bool = False
    color: str | None = None
    metadata: dict = Field(default_factory=dict)


class Marker(BaseModel):
    time: float
    name: str
    kind: Literal["section", "lyric", "cue", "loop"] = "section"


class StudioSession(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    modified_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    bpm: float = 120.0
    key: str | None = None
    meter: str = "4/4"
    sample_rate: int = 48000
    tracks: list[Track] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    loop_start: float | None = None
    loop_end: float | None = None
    chat_history: list[dict] = Field(default_factory=list)
    project_dna: dict = Field(default_factory=dict)
    generation_history: list[dict] = Field(default_factory=list)

    def touch(self):
        self.modified_at = datetime.now(timezone.utc).isoformat()

    def add_track(self, name: str, role: str = "other") -> Track:
        track = Track(name=name, role=role)
        self.tracks.append(track)
        self.touch()
        return track

    def find_track(self, track_id: str) -> Track:
        for track in self.tracks:
            if track.id == track_id:
                return track
        raise KeyError(track_id)

    def save(self, path: Path) -> None:
        self.touch()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "StudioSession":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "AutomationInterpolation",
    "AutomationLane",
    "AutomationPoint",
    "Clip",
    "Effect",
    "Marker",
    "Send",
    "StudioSession",
    "Track",
    "normalize_automation_parameter",
]
