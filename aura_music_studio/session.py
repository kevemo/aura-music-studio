from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class AutomationPoint(BaseModel):
    time: float
    value: float


class AutomationLane(BaseModel):
    parameter: str
    points: list[AutomationPoint] = Field(default_factory=list)


class Effect(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: Literal[
        "gain", "eq", "highpass", "lowpass", "compressor", "limiter", "gate", "deesser",
        "reverb", "delay", "distortion", "saturation", "exciter", "chorus", "flanger", "phaser",
        "tremolo", "pitch_shift", "doubler", "convolution", "stereo_width", "custom_safe_chain"
    ]
    enabled: bool = True
    parameters: dict[str, float | str | bool] = Field(default_factory=dict)


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
        "master", "vocals", "backing_vocals", "drums", "bass", "guitar", "piano", "keyboard",
        "strings", "synth", "percussion", "brass", "woodwinds", "fx", "midi", "other"
    ] = "other"
    clips: list[Clip] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    automation: list[AutomationLane] = Field(default_factory=list)
    volume_db: float = 0.0
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    mute: bool = False
    solo: bool = False
    color: str | None = None


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
