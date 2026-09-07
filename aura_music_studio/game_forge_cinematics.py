from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .game_forge_assets import find_game_asset
from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game, remove_public_snapshot, save_game
from .game_forge_world import load_world
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Cinematics"])

MAX_CINEMATIC_SECONDS = 600.0
MAX_TRANSFORM_TRACKS = 32
MAX_KEYFRAMES_PER_TRACK = 120
MAX_CUES = 128
MAX_PARTICLES_PER_CUE = 256

EasingName = Literal["linear", "ease_in", "ease_out", "ease_in_out"]
TrackKind = Literal["camera", "entity"]
CueKind = Literal["audio", "subtitle", "fade", "vfx"]
VFXPreset = Literal[
    "spark",
    "cosmic_burst",
    "glow",
    "trail",
    "smoke",
    "dust",
    "energy_ring",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CinematicVec3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)
    z: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)


class CinematicKeyframe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_s: float = Field(ge=0, le=MAX_CINEMATIC_SECONDS)
    position: CinematicVec3 | None = None
    rotation_deg: CinematicVec3 | None = None
    scale: CinematicVec3 | None = None
    look_at_entity_id: str | None = Field(default=None, min_length=1, max_length=120)
    easing: EasingName = "ease_in_out"

    @model_validator(mode="after")
    def validate_transform(self):
        if not any((self.position, self.rotation_deg, self.scale, self.look_at_entity_id)):
            raise ValueError("A cinematic keyframe must change position, rotation, scale or look-at target")
        if self.scale is not None and min(abs(self.scale.x), abs(self.scale.y), abs(self.scale.z)) < 0.000001:
            raise ValueError("Cinematic keyframe scale cannot contain a zero axis")
        return self


class CinematicTransformTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"track_{uuid4().hex}", min_length=3, max_length=120)
    kind: TrackKind
    target_entity_id: str = Field(min_length=1, max_length=120)
    keyframes: list[CinematicKeyframe] = Field(min_length=1, max_length=MAX_KEYFRAMES_PER_TRACK)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_keyframe_order(self):
        times = [row.time_s for row in self.keyframes]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("Cinematic keyframe times must be unique and ascending")
        return self


class CinematicCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"cue_{uuid4().hex}", min_length=3, max_length=120)
    kind: CueKind
    time_s: float = Field(ge=0, le=MAX_CINEMATIC_SECONDS)
    duration_s: float = Field(default=1.0, gt=0, le=MAX_CINEMATIC_SECONDS)
    target_entity_id: str | None = Field(default=None, min_length=1, max_length=120)
    asset_id: str | None = Field(default=None, min_length=1, max_length=160)
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    vfx_preset: VFXPreset | None = None
    color: str = Field(default="#ffffff", pattern=r"^#[0-9A-Fa-f]{6}$")
    intensity: float = Field(default=1.0, ge=0, le=10)
    particle_count: int = Field(default=36, ge=1, le=MAX_PARTICLES_PER_CUE)
    from_value: float = Field(default=0.0, ge=0, le=1)
    to_value: float = Field(default=1.0, ge=0, le=1)
    volume: float = Field(default=1.0, ge=0, le=4)
    loop: bool = False

    @model_validator(mode="after")
    def validate_kind_contract(self):
        if self.kind == "audio" and not self.asset_id:
            raise ValueError("Audio cinematic cues require a verified game audio asset")
        if self.kind == "subtitle" and not self.text:
            raise ValueError("Subtitle cinematic cues require text")
        if self.kind == "vfx" and not self.vfx_preset:
            raise ValueError("VFX cinematic cues require a built-in VFX preset")
        if self.kind != "audio" and self.asset_id is not None:
            raise ValueError("Only audio cinematic cues may reference a media asset")
        if self.kind != "subtitle" and self.text is not None:
            raise ValueError("Only subtitle cinematic cues may contain text")
        if self.kind != "vfx" and self.vfx_preset is not None:
            raise ValueError("Only VFX cinematic cues may select a VFX preset")
        return self


class CinematicTimelineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Main Cinematic", min_length=1, max_length=160)
    duration_s: float = Field(default=10.0, gt=0, le=MAX_CINEMATIC_SECONDS)
    autoplay: bool = False
    transform_tracks: list[CinematicTransformTrack] = Field(default_factory=list, max_length=MAX_TRANSFORM_TRACKS)
    cues: list[CinematicCue] = Field(default_factory=list, max_length=MAX_CUES)

    @model_validator(mode="after")
    def validate_timeline_bounds(self):
        track_ids = [row.id for row in self.transform_tracks]
        cue_ids = [row.id for row in self.cues]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("Cinematic transform track IDs must be unique")
        if len(cue_ids) != len(set(cue_ids)):
            raise ValueError("Cinematic cue IDs must be unique")
        for track in self.transform_tracks:
            if track.keyframes[-1].time_s > self.duration_s:
                raise ValueError(f"Track '{track.id}' exceeds the cinematic duration")
        for cue in self.cues:
            if cue.time_s + cue.duration_s > self.duration_s + 0.000001:
                raise ValueError(f"Cue '{cue.id}' exceeds the cinematic duration")
        return self


class CinematicTimeline(CinematicTimelineUpdate):
    schema_version: int = 1
    game_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


def cinematic_path(game_id: str) -> Path:
    root = game_dir(game_id).resolve()
    path = (root / "cinematic_dna.json").resolve()
    if root not in path.parents:
        raise ValueError("Cinematic DNA storage escaped the game directory")
    return path


def load_cinematic_optional(game_id: str) -> CinematicTimeline | None:
    path = cinematic_path(game_id)
    if not path.is_file():
        return None
    timeline = CinematicTimeline.model_validate_json(path.read_text(encoding="utf-8"))
    if timeline.game_id != game_id:
        raise ValueError("Cinematic DNA game identity mismatch")
    return timeline


def _entity_map(game_id: str):
    world = load_world(game_id)
    return world, {row.id: row for row in world.entities}


def cinematic_reference_blockers(game_id: str, timeline: CinematicTimeline | None = None) -> list[str]:
    timeline = timeline or load_cinematic_optional(game_id)
    if timeline is None:
        return []
    blockers: list[str] = []
    try:
        _world, entities = _entity_map(game_id)
    except (FileNotFoundError, ValueError):
        return ["Cinematic DNA requires a valid Aura World DNA document."]

    for track in timeline.transform_tracks:
        entity = entities.get(track.target_entity_id)
        if entity is None:
            blockers.append(f"Cinematic track '{track.id}' references missing entity '{track.target_entity_id}'.")
            continue
        if track.kind == "camera" and entity.kind != "camera":
            blockers.append(f"Cinematic camera track '{track.id}' must target a camera entity.")
        for keyframe in track.keyframes:
            if keyframe.look_at_entity_id and keyframe.look_at_entity_id not in entities:
                blockers.append(
                    f"Cinematic track '{track.id}' looks at missing entity '{keyframe.look_at_entity_id}'."
                )

    for cue in timeline.cues:
        if cue.target_entity_id and cue.target_entity_id not in entities:
            blockers.append(f"Cinematic cue '{cue.id}' references missing entity '{cue.target_entity_id}'.")
        if cue.kind == "audio" and cue.asset_id:
            try:
                asset = find_game_asset(game_id, cue.asset_id)
            except FileNotFoundError:
                blockers.append(f"Cinematic audio cue '{cue.id}' references a missing game asset.")
            else:
                if asset.kind not in {"audio", "music"}:
                    blockers.append(f"Cinematic audio cue '{cue.id}' must use an audio or music asset.")
    return blockers


def _candidate_timeline(game_id: str, body: CinematicTimelineUpdate) -> CinematicTimeline:
    previous = load_cinematic_optional(game_id)
    return CinematicTimeline(
        game_id=game_id,
        revision=(previous.revision + 1) if previous else 1,
        created_at=previous.created_at if previous else _now(),
        updated_at=_now(),
        **body.model_dump(mode="python"),
    )


def save_cinematic(game_id: str, body: CinematicTimelineUpdate) -> CinematicTimeline:
    timeline = _candidate_timeline(game_id, body)
    blockers = cinematic_reference_blockers(game_id, timeline)
    if blockers:
        raise ValueError("; ".join(blockers))
    path = cinematic_path(game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return timeline


def delete_cinematic(game_id: str) -> bool:
    path = cinematic_path(game_id)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def cinematic_integrity_payload(game_id: str) -> dict | None:
    timeline = load_cinematic_optional(game_id)
    if timeline is None:
        return None
    return timeline.model_dump(mode="json", exclude={"created_at", "updated_at"})


def cinematic_runtime_payload(game_id: str) -> dict | None:
    timeline = load_cinematic_optional(game_id)
    if timeline is None:
        return None
    blockers = cinematic_reference_blockers(game_id, timeline)
    if blockers:
        raise ValueError("; ".join(blockers))
    payload = timeline.model_dump(mode="json", exclude={"created_at", "updated_at"})
    payload["runtime_contract"] = {
        "declarative_only": True,
        "arbitrary_javascript": False,
        "arbitrary_shader_code": False,
        "network_access": False,
        "built_in_vfx_presets": list(VFXPreset.__args__),
        "max_particles_per_cue": MAX_PARTICLES_PER_CUE,
    }
    return payload


def _invalidate_after_cinematic_change(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game cinematic editing unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise HTTPException(409, "Reopen this game before changing its cinematic DNA.")


@router.get("/api/game-forge/games/{game_id}/cinematic")
def get_game_cinematic(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    timeline = load_cinematic_optional(game.id)
    return {
        "game_id": game.id,
        "cinematic": timeline.model_dump(mode="json") if timeline else None,
        "publication_blockers": cinematic_reference_blockers(game.id, timeline),
        "declarative_only": True,
        "arbitrary_code_allowed": False,
        "built_in_vfx_presets": list(VFXPreset.__args__),
    }


@router.put("/api/game-forge/games/{game_id}/cinematic")
def replace_game_cinematic(game_id: str, body: CinematicTimelineUpdate, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    try:
        timeline = save_cinematic(game.id, body)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    _invalidate_after_cinematic_change(game)
    return {
        "cinematic": timeline.model_dump(mode="json"),
        "invalidated_previous_build_and_rating": True,
        "declarative_only": True,
        "arbitrary_code_allowed": False,
    }


@router.delete("/api/game-forge/games/{game_id}/cinematic")
def remove_game_cinematic(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    _require_editable(game)
    deleted = delete_cinematic(game.id)
    if deleted:
        _invalidate_after_cinematic_change(game)
    return {
        "deleted": deleted,
        "invalidated_previous_build_and_rating": deleted,
    }


__all__ = [
    "CinematicCue",
    "CinematicKeyframe",
    "CinematicTimeline",
    "CinematicTimelineUpdate",
    "CinematicTransformTrack",
    "VFXPreset",
    "cinematic_integrity_payload",
    "cinematic_reference_blockers",
    "cinematic_runtime_payload",
    "delete_cinematic",
    "load_cinematic_optional",
    "router",
    "save_cinematic",
]
