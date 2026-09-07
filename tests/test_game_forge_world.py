from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_integrity import game_integrity_hash
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_runtime import build_private_playtest
from aura_music_studio.game_forge_world import (
    BehaviorNodeDNA,
    MaterialDNA,
    NATIVE_WORLD_CAPABILITIES,
    Vec3,
    generate_foundation_world,
    load_world,
    save_world,
    world_stream_index,
)
from aura_music_studio.plans import get_plan


def _member(plan_id: str = "base"):
    return SimpleNamespace(plan=get_plan(plan_id), user_id=f"world-user-{plan_id}")


def _patch_world_storage(monkeypatch, tmp_path):
    root = tmp_path / "games"
    root.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)
    import aura_music_studio.game_forge_world as world_module
    import aura_music_studio.game_forge_runtime as runtime_module

    monkeypatch.setattr(world_module, "game_dir", store.game_dir)
    monkeypatch.setattr(runtime_module, "game_dir", store.game_dir)
    monkeypatch.setattr(runtime_module, "save_game", store.save_game)
    return root


def test_aura_native_engines_are_the_primary_game_targets():
    from aura_music_studio.game_forge_models import ENGINE_REGISTRY

    assert GameDNA(title="2D", prompt="test").engine_target == "aura2d"
    assert ENGINE_REGISTRY["aura2d"]["native"] is True
    assert ENGINE_REGISTRY["aura3d"]["native"] is True
    for adapter in ("phaser4", "playcanvas", "babylon", "godot"):
        assert ENGINE_REGISTRY[adapter]["native"] is False
        assert "adapter" in ENGINE_REGISTRY[adapter]["role"].lower()


def test_world_generation_has_unreal_class_foundation_capability_families(monkeypatch, tmp_path):
    _patch_world_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Dragon World", prompt="A 3D dragon world", dimension="3d", engine_target="aura3d")
    store.create_game(_member(), game)
    world = generate_foundation_world(game)

    assert world.dimension == "3d"
    assert {"player", "camera", "sun", "ground"}.issubset({row.id for row in world.entities})
    assert world.terrain.mode == "heightfield"
    assert world.performance.target_fps >= 60
    assert world.metadata["arbitrary_script_source_allowed"] is False
    assert set(NATIVE_WORLD_CAPABILITIES) >= {
        "scene_graph",
        "world_streaming",
        "lod",
        "procedural_generation",
        "materials",
        "lighting",
        "physics",
        "animation",
        "behaviors",
        "terrain",
        "performance",
    }
    assert world_stream_index(world)


def test_behavior_graph_rejects_arbitrary_operation_names():
    with pytest.raises(ValidationError):
        BehaviorNodeDNA(op="eval_javascript", params={"source": "fetch('https://example.com')"})


def test_world_edit_changes_game_integrity_hash(monkeypatch, tmp_path):
    _patch_world_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Integrity World", prompt="A safe 3D game", dimension="3d", engine_target="aura3d", rights_confirmed=True)
    store.create_game(_member(), game)
    world = generate_foundation_world(game)
    before = game_integrity_hash(game)

    player = next(row for row in world.entities if row.id == "player")
    player.transform.position = Vec3(x=25, y=2, z=-11)
    player.material = MaterialDNA(shader="pbr", base_color="#ff7700", roughness=0.3)
    world.touch()
    save_world(world)

    assert game_integrity_hash(game) != before
    reloaded = load_world(game.id)
    assert next(row for row in reloaded.entities if row.id == "player").transform.position.x == 25


def test_private_build_hash_includes_world_revision(monkeypatch, tmp_path):
    _patch_world_storage(monkeypatch, tmp_path)
    game = GameDNA(title="Build World", prompt="A safe world", rights_confirmed=True)
    store.create_game(_member(), game)
    generate_foundation_world(game)
    game, _html = build_private_playtest(game)
    first_hash = game.latest_build.content_hash

    world = load_world(game.id)
    world.environment.fog_density = 0.2
    world.touch()
    save_world(world)
    assert game_integrity_hash(game) != first_hash


def test_world_integrity_routes_precede_foundation_game_routes():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "game_forge_world_api" in source
    assert source.index("app.include_router(game_forge_world_router)") < source.index("app.include_router(game_forge_router)")
    world_source = Path("aura_music_studio/game_forge_world_api.py").read_text(encoding="utf-8")
    assert '"/api/game-forge/games/{game_id}/scan"' in world_source
    assert '"/api/game-forge/games/{game_id}/publish-test"' in world_source
    assert "game_integrity_hash" in world_source
