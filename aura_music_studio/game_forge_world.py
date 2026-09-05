from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .game_forge_models import GameDNA, GameDimension
from .game_forge_store import game_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Vec3(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class TransformDNA(BaseModel):
    position: Vec3 = Field(default_factory=Vec3)
    rotation_deg: Vec3 = Field(default_factory=Vec3)
    scale: Vec3 = Field(default_factory=lambda: Vec3(x=1.0, y=1.0, z=1.0))


MaterialShader = Literal["pbr", "unlit", "toon", "emissive", "water", "terrain"]


class MaterialDNA(BaseModel):
    shader: MaterialShader = "pbr"
    base_color: str = "#8ea6ff"
    metallic: float = Field(default=0.0, ge=0.0, le=1.0)
    roughness: float = Field(default=0.7, ge=0.0, le=1.0)
    emissive_strength: float = Field(default=0.0, ge=0.0, le=100.0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    texture_refs: dict[str, str] = Field(default_factory=dict)


LightKind = Literal["directional", "point", "spot", "area", "sky"]


class LightDNA(BaseModel):
    kind: LightKind = "point"
    color: str = "#ffffff"
    intensity: float = Field(default=1.0, ge=0.0, le=1_000_000.0)
    range: float = Field(default=25.0, ge=0.0, le=1_000_000.0)
    cast_shadows: bool = True
    temperature_kelvin: float | None = Field(default=None, ge=1000.0, le=20000.0)


PhysicsShape = Literal["none", "box", "sphere", "capsule", "mesh", "heightfield"]
PhysicsMode = Literal["static", "dynamic", "kinematic", "trigger"]


class PhysicsBodyDNA(BaseModel):
    mode: PhysicsMode = "static"
    shape: PhysicsShape = "box"
    mass_kg: float = Field(default=1.0, ge=0.0, le=1_000_000.0)
    friction: float = Field(default=0.6, ge=0.0, le=10.0)
    restitution: float = Field(default=0.0, ge=0.0, le=1.0)
    gravity_scale: float = Field(default=1.0, ge=-10.0, le=10.0)
    collision_layer: str = Field(default="world", max_length=80)
    collision_mask: list[str] = Field(default_factory=lambda: ["world", "player"], max_length=32)


BehaviorOp = Literal[
    "player_input",
    "camera_follow",
    "collectible",
    "damage",
    "patrol",
    "follow_target",
    "dialogue",
    "quest_trigger",
    "spawn",
    "timer",
    "state_machine",
    "door",
    "checkpoint",
    "audio_zone",
    "particle_emitter",
]


class BehaviorNodeDNA(BaseModel):
    id: str = Field(default_factory=lambda: _id("behavior"))
    op: BehaviorOp
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AnimationStateDNA(BaseModel):
    state: str = Field(default="idle", max_length=80)
    clip_ref: str | None = Field(default=None, max_length=240)
    speed: float = Field(default=1.0, ge=0.0, le=20.0)
    loop: bool = True
    blend_seconds: float = Field(default=0.15, ge=0.0, le=10.0)


EntityKind = Literal[
    "player",
    "npc",
    "camera",
    "light",
    "terrain",
    "mesh",
    "sprite",
    "collectible",
    "hazard",
    "trigger",
    "spawn",
    "audio",
    "vfx",
    "ui_anchor",
]


class WorldEntityDNA(BaseModel):
    id: str = Field(default_factory=lambda: _id("entity"))
    name: str = Field(default="Entity", min_length=1, max_length=160)
    kind: EntityKind = "mesh"
    parent_id: str | None = None
    transform: TransformDNA = Field(default_factory=TransformDNA)
    visible: bool = True
    active: bool = True
    tags: list[str] = Field(default_factory=list, max_length=40)
    asset_ref: str | None = Field(default=None, max_length=300)
    material: MaterialDNA | None = None
    light: LightDNA | None = None
    physics: PhysicsBodyDNA | None = None
    animation: AnimationStateDNA | None = None
    behaviors: list[BehaviorNodeDNA] = Field(default_factory=list, max_length=40)
    lod_group: str = Field(default="default", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TerrainDNA(BaseModel):
    mode: Literal["flat", "heightfield", "mesh", "voxel"] = "flat"
    width: float = Field(default=512.0, gt=0.0, le=1_000_000.0)
    depth: float = Field(default=512.0, gt=0.0, le=1_000_000.0)
    max_height: float = Field(default=80.0, ge=0.0, le=100_000.0)
    seed: int = 1
    biome: str = Field(default="temperate", max_length=120)
    procedural_layers: list[str] = Field(default_factory=list, max_length=50)


class PerformanceBudgetDNA(BaseModel):
    target_fps: int = Field(default=60, ge=24, le=240)
    max_visible_entities: int = Field(default=3000, ge=10, le=500_000)
    max_dynamic_lights: int = Field(default=32, ge=0, le=4096)
    max_draw_calls: int = Field(default=2500, ge=50, le=200_000)
    triangle_budget: int = Field(default=4_000_000, ge=1_000, le=2_000_000_000)
    texture_memory_mb: int = Field(default=1024, ge=64, le=131_072)
    streaming_cell_size: float = Field(default=128.0, gt=1.0, le=100_000.0)
    streaming_radius_cells: int = Field(default=2, ge=1, le=32)
    lod_distances: list[float] = Field(default_factory=lambda: [30.0, 80.0, 180.0, 400.0], max_length=12)


class WorldEnvironmentDNA(BaseModel):
    gravity: Vec3 = Field(default_factory=lambda: Vec3(x=0.0, y=-9.81, z=0.0))
    ambient_color: str = "#4e5f8d"
    exposure: float = Field(default=1.0, ge=0.01, le=32.0)
    fog_density: float = Field(default=0.0, ge=0.0, le=1.0)
    sky_mode: Literal["gradient", "physical", "hdri", "space"] = "physical"
    dynamic_gi_requested: bool = True
    reflections_requested: bool = True
    virtual_shadowing_requested: bool = True


class ProceduralRuleDNA(BaseModel):
    id: str = Field(default_factory=lambda: _id("pcg"))
    operation: Literal["scatter", "spline_scatter", "biome_fill", "road", "building", "terrain_noise", "quest_population"]
    target_tag: str = Field(default="world", max_length=80)
    density: float = Field(default=1.0, ge=0.0, le=1000.0)
    seed: int = 1
    params: dict[str, Any] = Field(default_factory=dict)


class GameWorldDNA(BaseModel):
    schema_version: int = 1
    world_id: str = Field(default_factory=lambda: _id("world"))
    game_id: str
    dimension: GameDimension
    name: str = Field(default="Main World", min_length=1, max_length=160)
    units_per_meter: float = Field(default=1.0, gt=0.0, le=10_000.0)
    environment: WorldEnvironmentDNA = Field(default_factory=WorldEnvironmentDNA)
    terrain: TerrainDNA = Field(default_factory=TerrainDNA)
    performance: PerformanceBudgetDNA = Field(default_factory=PerformanceBudgetDNA)
    entities: list[WorldEntityDNA] = Field(default_factory=list, max_length=50_000)
    procedural_rules: list[ProceduralRuleDNA] = Field(default_factory=list, max_length=500)
    revision: int = 1
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.revision += 1
        self.updated_at = _now()


NATIVE_WORLD_CAPABILITIES = {
    "scene_graph": "Aura Scene Graph",
    "world_streaming": "Aura World Cells",
    "lod": "Aura Adaptive Detail",
    "procedural_generation": "Aura Procedural World Graph",
    "materials": "Aura Material DNA",
    "lighting": "Aura Dynamic Lighting Abstraction",
    "physics": "Aura Physics DNA",
    "animation": "Aura Animation State Graph",
    "behaviors": "Aura Safe Behavior Graph",
    "terrain": "Aura Terrain DNA",
    "performance": "Aura Performance Budgeter",
}


def validate_world(world: GameWorldDNA) -> None:
    if len(world.entities) > world.performance.max_visible_entities * 20:
        raise ValueError("World entity count exceeds the configured streaming safety envelope")
    ids = [row.id for row in world.entities]
    if len(ids) != len(set(ids)):
        raise ValueError("World entity IDs must be unique")
    known = set(ids)
    for entity in world.entities:
        if entity.parent_id and entity.parent_id not in known:
            raise ValueError(f"Entity {entity.id} has an unknown parent")
        if entity.parent_id == entity.id:
            raise ValueError("An entity cannot parent itself")
        scale = entity.transform.scale
        if min(abs(scale.x), abs(scale.y), abs(scale.z)) < 0.000001:
            raise ValueError(f"Entity {entity.id} has a zero scale axis")
        for node in entity.behaviors:
            # Pydantic's Literal already constrains the operation. This explicit check documents
            # that Game Forge behavior graphs are data, not JavaScript/Python source.
            if not node.op:
                raise ValueError("Behavior operation is required")
    if world.performance.streaming_cell_size <= 0:
        raise ValueError("World streaming cell size must be positive")


def world_path(game_id: str):
    return game_dir(game_id) / "world_dna.json"


def save_world(world: GameWorldDNA) -> GameWorldDNA:
    validate_world(world)
    path = world_path(world.game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(world.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return world


def load_world(game_id: str) -> GameWorldDNA:
    path = world_path(game_id)
    if not path.is_file():
        raise FileNotFoundError(path)
    world = GameWorldDNA.model_validate_json(path.read_text(encoding="utf-8"))
    validate_world(world)
    return world


def load_world_optional(game_id: str) -> GameWorldDNA | None:
    try:
        return load_world(game_id)
    except FileNotFoundError:
        return None


def world_rating_payload(game_id: str) -> dict[str, Any] | None:
    world = load_world_optional(game_id)
    if world is None:
        return None
    # Exclude timestamps; include all creator/Aura-controlled gameplay/world state that could
    # materially change a playtest or content assessment.
    return world.model_dump(mode="json", exclude={"created_at", "updated_at"})


def world_content_hash(game_id: str) -> str | None:
    payload = world_rating_payload(game_id)
    if payload is None:
        return None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _foundation_entities(game: GameDNA) -> list[WorldEntityDNA]:
    if game.dimension == "2d":
        return [
            WorldEntityDNA(
                id="player",
                name="Player",
                kind="player",
                transform=TransformDNA(position=Vec3(x=0, y=0, z=0)),
                material=MaterialDNA(shader="unlit", base_color="#69e4ff"),
                physics=PhysicsBodyDNA(mode="dynamic", shape="capsule"),
                behaviors=[BehaviorNodeDNA(op="player_input", params={"scheme": "wasd_arrows_gamepad"})],
            ),
            WorldEntityDNA(
                id="camera",
                name="Main Camera",
                kind="camera",
                behaviors=[BehaviorNodeDNA(op="camera_follow", params={"target": "player", "smoothing": 0.12})],
            ),
            WorldEntityDNA(id="spawn", name="Player Spawn", kind="spawn", transform=TransformDNA(position=Vec3(x=0, y=0, z=0))),
        ]
    return [
        WorldEntityDNA(
            id="player",
            name="Player",
            kind="player",
            transform=TransformDNA(position=Vec3(x=0, y=1.1, z=0)),
            material=MaterialDNA(shader="pbr", base_color="#69e4ff", roughness=0.42),
            physics=PhysicsBodyDNA(mode="dynamic", shape="capsule", mass_kg=80),
            behaviors=[BehaviorNodeDNA(op="player_input", params={"scheme": "third_person"})],
        ),
        WorldEntityDNA(
            id="camera",
            name="Main Camera",
            kind="camera",
            transform=TransformDNA(position=Vec3(x=0, y=3.2, z=7.5)),
            behaviors=[BehaviorNodeDNA(op="camera_follow", params={"target": "player", "orbit": True})],
        ),
        WorldEntityDNA(
            id="sun",
            name="Primary Sun",
            kind="light",
            transform=TransformDNA(rotation_deg=Vec3(x=-48, y=28, z=0)),
            light=LightDNA(kind="directional", intensity=4.0, cast_shadows=True, temperature_kelvin=5600),
        ),
        WorldEntityDNA(
            id="ground",
            name="Ground",
            kind="terrain",
            material=MaterialDNA(shader="terrain", base_color="#526d45", roughness=0.9),
            physics=PhysicsBodyDNA(mode="static", shape="heightfield"),
        ),
        WorldEntityDNA(id="spawn", name="Player Spawn", kind="spawn", transform=TransformDNA(position=Vec3(x=0, y=1.1, z=0))),
    ]


def generate_foundation_world(game: GameDNA) -> GameWorldDNA:
    terrain = TerrainDNA(
        mode="flat" if game.dimension == "2d" else "heightfield",
        width=512 if game.dimension == "2d" else 4096,
        depth=512 if game.dimension == "2d" else 4096,
        max_height=0 if game.dimension == "2d" else 180,
        seed=int(hashlib.sha256(game.id.encode("utf-8")).hexdigest()[:8], 16),
        biome=(game.genre or "temperate")[:120],
        procedural_layers=[] if game.dimension == "2d" else ["terrain_noise", "biome_scatter"],
    )
    performance = PerformanceBudgetDNA(
        target_fps=60,
        max_visible_entities=2500 if game.dimension == "2d" else 1800,
        max_dynamic_lights=8 if game.dimension == "2d" else 48,
        max_draw_calls=1800 if game.dimension == "2d" else 3500,
        triangle_budget=500_000 if game.dimension == "2d" else 8_000_000,
        texture_memory_mb=512 if game.dimension == "2d" else 2048,
        streaming_cell_size=96 if game.dimension == "2d" else 256,
        streaming_radius_cells=2,
    )
    world = GameWorldDNA(
        game_id=game.id,
        dimension=game.dimension,
        name=f"{game.title} · Main World"[:160],
        terrain=terrain,
        performance=performance,
        entities=_foundation_entities(game),
        procedural_rules=(
            []
            if game.dimension == "2d"
            else [
                ProceduralRuleDNA(operation="terrain_noise", target_tag="terrain", density=1.0, seed=terrain.seed),
                ProceduralRuleDNA(operation="biome_fill", target_tag="world", density=0.35, seed=terrain.seed + 1),
            ]
        ),
        metadata={
            "aura_native_world": True,
            "arbitrary_script_source_allowed": False,
            "streaming_model": "cell_partition_v1",
            "capabilities": NATIVE_WORLD_CAPABILITIES,
        },
    )
    return save_world(world)


def ensure_world(game: GameDNA) -> GameWorldDNA:
    world = load_world_optional(game.id)
    if world is None:
        return generate_foundation_world(game)
    if world.dimension != game.dimension:
        raise ValueError("Stored world dimension no longer matches Game DNA; create a migrated world revision")
    return world


def world_stream_index(world: GameWorldDNA) -> dict[str, list[str]]:
    size = world.performance.streaming_cell_size
    cells: dict[str, list[str]] = {}
    for entity in world.entities:
        p = entity.transform.position
        cx = math.floor(p.x / size)
        cy = math.floor(p.y / size)
        cz = math.floor(p.z / size)
        key = f"{cx}:{cy}:{cz}"
        cells.setdefault(key, []).append(entity.id)
    return {key: sorted(value) for key, value in sorted(cells.items())}


def world_summary(world: GameWorldDNA) -> dict[str, Any]:
    return {
        "world_id": world.world_id,
        "game_id": world.game_id,
        "dimension": world.dimension,
        "name": world.name,
        "revision": world.revision,
        "entities": len(world.entities),
        "procedural_rules": len(world.procedural_rules),
        "streaming_cells": len(world_stream_index(world)),
        "terrain": world.terrain.model_dump(mode="json"),
        "performance": world.performance.model_dump(mode="json"),
        "native_capabilities": NATIVE_WORLD_CAPABILITIES,
        "arbitrary_script_source_allowed": False,
    }
