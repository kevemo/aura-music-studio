from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .game_forge_assets import (
    asset_publication_blockers,
    private_runtime_asset_path,
    runtime_asset_manifest,
)
from .game_forge_integrity import game_integrity_hash
from .game_forge_models import GameDNA
from .game_forge_store import game_dir
from .game_forge_world import ensure_world

GODOT_SOURCE_ADAPTER_VERSION = 1


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _write_entry(zf: ZipFile, name: str, data: bytes) -> None:
    clean = str(name or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        raise ValueError("Unsafe Godot source archive path")
    zf.writestr(_zip_info(clean), data)


def _exports_root(game_id: str) -> Path:
    parent = game_dir(game_id).resolve()
    target = (parent / "exports").resolve()
    if parent not in target.parents:
        raise ValueError("Godot export storage escaped the game directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _export_path(game_id: str, export_id: str) -> Path:
    if not export_id.startswith("export_") or not export_id[7:].isalnum() or len(export_id) > 80:
        raise ValueError("Invalid Godot source export id")
    root = _exports_root(game_id).resolve()
    target = (root / f"{export_id}.zip").resolve()
    if root not in target.parents:
        raise ValueError("Godot source export path escaped storage")
    return target


def _validate_source_exportable(game: GameDNA) -> str:
    if not game.rights_confirmed or not str(game.rights_attestation or "").strip():
        raise ValueError("Confirm the project's creation/usage rights before Godot source export")
    current_hash = game_integrity_hash(game)
    if not game.latest_build or game.latest_build.content_hash != current_hash:
        raise ValueError("Game build is missing or stale. Rebuild the current game before Godot source export")
    blockers = asset_publication_blockers(game.id)
    if blockers:
        raise ValueError("; ".join(blockers))
    return current_hash


def _godot_string(value: str, limit: int = 160) -> str:
    text = str(value or "")[:limit].replace("\\", "\\\\").replace('"', '\\"')
    return text.replace("\r", " ").replace("\n", " ")


def _portable_world_payload(world) -> dict:
    """Project World DNA into deterministic portable adapter data.

    The authoritative content hash already binds the persisted World DNA. Godot does not consume
    storage identity or audit timestamps, so exporting those volatile fields would make identical
    integrity-bound builds produce different ZIP bytes without changing playable behavior.
    """
    return world.model_dump(
        mode="json",
        exclude={"world_id", "created_at", "updated_at"},
    )


def _project_godot(game: GameDNA) -> bytes:
    title = _godot_string(game.title)
    content = f"""; Generated deterministically by Elevate Souls Productions Content Creation Command Center.
; Godot 4 source adapter developer preview. The Aura runtime remains authoritative.
config_version=5

[application]
config/name=\"{title}\"
run/main_scene=\"res://main.tscn\"

[display]
window/size/viewport_width=1280
window/size/viewport_height=720
window/size/window_width_override=1280
window/size/window_height_override=720
window/stretch/mode=\"canvas_items\"

[rendering]
renderer/rendering_method=\"gl_compatibility\"
renderer/rendering_method.mobile=\"gl_compatibility\"
environment/defaults/default_clear_color=Color(0.012, 0.008, 0.027, 1)
"""
    return content.encode("utf-8")


def _main_scene() -> bytes:
    return b'''[gd_scene load_steps=2 format=3]\n\n[ext_resource type="Script" path="res://main.gd" id="1_aura"]\n\n[node name="AuraGodotAdapter" type="Node"]\nscript = ExtResource("1_aura")\n'''


def _main_script() -> bytes:
    # This is a fixed reviewed adapter template. Game/creator text is data in JSON files and is
    # never interpolated into executable GDScript.
    return r'''extends Node

const SCALE_2D := 42.0
var game_data: Dictionary = {}
var world_data: Dictionary = {}
var player_2d: Polygon2D
var player_3d: Node3D

func _ready() -> void:
    game_data = _load_json("res://game_dna.json")
    world_data = _load_json("res://world_dna.json")
    if game_data.is_empty() or world_data.is_empty():
        push_error("Aura Godot adapter data is missing or invalid")
        return
    if String(game_data.get("dimension", "2d")) == "3d":
        _build_3d()
    else:
        _build_2d()

func _process(delta: float) -> void:
    var direction := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
    if is_instance_valid(player_2d):
        player_2d.position += direction * 220.0 * delta
    if is_instance_valid(player_3d):
        player_3d.position += Vector3(direction.x, 0.0, direction.y) * 6.0 * delta

func _load_json(path: String) -> Dictionary:
    var text := FileAccess.get_file_as_string(path)
    if text.is_empty():
        return {}
    var parsed = JSON.parse_string(text)
    return parsed if typeof(parsed) == TYPE_DICTIONARY else {}

func _entities() -> Array:
    var rows = world_data.get("entities", [])
    return rows if typeof(rows) == TYPE_ARRAY else []

func _vec3(raw, fallback := Vector3.ZERO) -> Vector3:
    if typeof(raw) != TYPE_DICTIONARY:
        return fallback
    return Vector3(float(raw.get("x", fallback.x)), float(raw.get("y", fallback.y)), float(raw.get("z", fallback.z)))

func _is_visible_world_entity(row: Dictionary) -> bool:
    return bool(row.get("active", true)) and bool(row.get("visible", true)) and String(row.get("kind", "mesh")) not in ["camera", "light", "audio", "vfx", "ui_anchor"]

func _color_for(kind: String) -> Color:
    match kind:
        "player": return Color("69e4ff")
        "collectible": return Color("f6d36c")
        "hazard": return Color("ff627f")
        "trigger": return Color("986cff")
        "npc": return Color("79dda5")
        "terrain": return Color("526d45")
        _: return Color("8ea6ff")

func _build_2d() -> void:
    var root := Node2D.new()
    root.name = "AuraWorld2D"
    add_child(root)
    var camera := Camera2D.new()
    camera.position = Vector2.ZERO
    root.add_child(camera)
    for raw in _entities():
        if typeof(raw) != TYPE_DICTIONARY:
            continue
        var row: Dictionary = raw
        if not _is_visible_world_entity(row):
            continue
        var transform = row.get("transform", {})
        var pos := _vec3(transform.get("position", {})) if typeof(transform) == TYPE_DICTIONARY else Vector3.ZERO
        var marker := Polygon2D.new()
        marker.name = String(row.get("name", "Entity"))
        marker.polygon = PackedVector2Array([Vector2(-12,-12), Vector2(12,-12), Vector2(12,12), Vector2(-12,12)])
        marker.color = _color_for(String(row.get("kind", "mesh")))
        marker.position = Vector2(pos.x * SCALE_2D, -pos.y * SCALE_2D)
        root.add_child(marker)
        if String(row.get("kind", "")) == "player" or String(row.get("id", "")) == "player":
            player_2d = marker
    if not is_instance_valid(player_2d):
        player_2d = Polygon2D.new()
        player_2d.polygon = PackedVector2Array([Vector2(-12,-12), Vector2(12,-12), Vector2(12,12), Vector2(-12,12)])
        player_2d.color = _color_for("player")
        root.add_child(player_2d)

func _build_3d() -> void:
    var root := Node3D.new()
    root.name = "AuraWorld3D"
    add_child(root)
    var camera := Camera3D.new()
    camera.position = Vector3(0, 8, 14)
    camera.look_at_from_position(camera.position, Vector3.ZERO, Vector3.UP)
    root.add_child(camera)
    var sun := DirectionalLight3D.new()
    sun.rotation_degrees = Vector3(-48, 28, 0)
    sun.light_energy = 1.5
    root.add_child(sun)
    for raw in _entities():
        if typeof(raw) != TYPE_DICTIONARY:
            continue
        var row: Dictionary = raw
        if not _is_visible_world_entity(row):
            continue
        var transform = row.get("transform", {})
        var pos := _vec3(transform.get("position", {})) if typeof(transform) == TYPE_DICTIONARY else Vector3.ZERO
        var marker := MeshInstance3D.new()
        marker.name = String(row.get("name", "Entity"))
        var box := BoxMesh.new()
        box.size = Vector3(1.0, 1.0, 1.0)
        marker.mesh = box
        var material := StandardMaterial3D.new()
        material.albedo_color = _color_for(String(row.get("kind", "mesh")))
        marker.material_override = material
        marker.position = pos
        root.add_child(marker)
        if String(row.get("kind", "")) == "player" or String(row.get("id", "")) == "player":
            player_3d = marker
    if not is_instance_valid(player_3d):
        player_3d = Node3D.new()
        root.add_child(player_3d)
'''.encode("utf-8")


def _readme(game: GameDNA, content_hash: str) -> bytes:
    text = f"""# {game.title} — Aura → Godot 4 source adapter preview

Generated by Elevate Souls Productions Content Creation Command Center — Powered by Aura AI.

This is a deterministic **developer-preview source project**, not a production-equivalent port of the Aura runtime.
The authoritative tested game remains the Aura build identified below.

- Aura game id: `{game.id}`
- Aura build id: `{game.latest_build.build_id}`
- Aura runtime: `{game.latest_build.runtime}`
- Integrity hash: `{content_hash}`
- Adapter version: `{GODOT_SOURCE_ADAPTER_VERSION}`
- Godot target: 4.x

## What this preview does

- Opens as a Godot 4 project with `project.godot` and `main.tscn`.
- Reads integrity-bound `game_dna.json` and `world_dna.json` as data.
- Creates a basic 2D or 3D representation of active visible World DNA entities.
- Provides keyboard movement through Godot's built-in `ui_*` actions.
- Includes checksum-verified game media under `media/` plus `assets.json` for manual/native adapter work.

## Deliberate parity limits

Gameplay/physics, Adventure State, World Events, State Machines, cinematics, model bindings, spatial audio and other Aura-native systems are **not yet claimed as equivalent Godot behavior** in this preview. Creator text never becomes generated GDScript; `main.gd` is a fixed reviewed adapter template.

## Validate with Godot 4

Open this directory in the Godot 4 editor. For automated validation on a machine with Godot 4 installed, run:

```sh
godot --headless --path . --quit-after 1
```

Production-ready status remains false until the Command Center CI itself runs a pinned Godot 4 headless validation gate for generated projects.
"""
    return text.encode("utf-8")


def create_godot_source_export(game: GameDNA) -> dict:
    content_hash = _validate_source_exportable(game)
    world = ensure_world(game)
    assets = runtime_asset_manifest(game.id)
    media_entries: list[dict] = []
    media_bytes: list[tuple[str, bytes]] = []
    for row in assets:
        media_url = str(row.get("media_url") or "")
        if not media_url.startswith("media/"):
            raise ValueError("Godot source media URL escaped the same-origin game-asset contract")
        filename = media_url.split("/", 1)[1]
        path, record = private_runtime_asset_path(game.id, filename)
        data = path.read_bytes()
        if len(data) != int(row.get("byte_size") or -1) or _sha256_bytes(data) != str(row.get("sha256") or ""):
            raise ValueError(f"Game asset '{record.label}' failed Godot source integrity verification")
        media_entries.append(dict(row))
        media_bytes.append((media_url, data))

    seed = f"godot4_source:v{GODOT_SOURCE_ADAPTER_VERSION}:{game.id}:{content_hash}".encode("utf-8")
    export_id = f"export_{hashlib.sha256(seed).hexdigest()[:32]}"
    adapter_manifest = {
        "schema_version": GODOT_SOURCE_ADAPTER_VERSION,
        "adapter": "godot4_source_preview",
        "production_ready": False,
        "source_project_ready": True,
        "godot_major": 4,
        "game_id": game.id,
        "build_id": game.latest_build.build_id,
        "aura_runtime": game.latest_build.runtime,
        "content_hash": content_hash,
        "verified_media": media_entries,
        "creator_generated_executable_code": False,
        "fixed_reviewed_gdscript_template": True,
        "runtime_parity_claimed": False,
        "requires_headless_engine_validation_before_production": True,
    }
    game_payload = {
        "id": game.id,
        "title": game.title,
        "genre": game.genre,
        "dimension": game.dimension,
        "engine_target": game.engine_target,
        "mechanics": game.mechanics,
        "controls": game.controls,
        "scenes": game.scenes,
        "content_hash": content_hash,
    }
    world_payload = _portable_world_payload(world)

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
        _write_entry(zf, "project.godot", _project_godot(game))
        _write_entry(zf, "main.tscn", _main_scene())
        _write_entry(zf, "main.gd", _main_script())
        _write_entry(zf, "README.md", _readme(game, content_hash))
        _write_entry(zf, "adapter_manifest.json", (json.dumps(adapter_manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        _write_entry(zf, "game_dna.json", (json.dumps(game_payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        _write_entry(zf, "world_dna.json", (json.dumps(world_payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        _write_entry(zf, "assets.json", (json.dumps(media_entries, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        for name, data in sorted(media_bytes, key=lambda item: item[0]):
            _write_entry(zf, name, data)

    payload = buffer.getvalue()
    target = _export_path(game.id, export_id)
    tmp = target.with_suffix(".zip.tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)
    return {
        "export_id": export_id,
        "target": "godot",
        "format": "godot4_source_zip_v1",
        "filename": target.name,
        "byte_size": len(payload),
        "sha256": _sha256_bytes(payload),
        "content_hash": content_hash,
        "asset_count": len(media_entries),
        "download_url": f"/api/game-forge/games/{game.id}/exports/{export_id}/download",
        "source_project_ready": True,
        "production_ready": False,
        "runtime_parity_claimed": False,
        "requires_headless_engine_validation_before_production": True,
        "creator_generated_executable_code": False,
    }


__all__ = ["GODOT_SOURCE_ADAPTER_VERSION", "create_godot_source_export"]