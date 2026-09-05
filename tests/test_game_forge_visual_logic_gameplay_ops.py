from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_gameplay import gameplay_runtime_payload
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_visual_logic import (
    PutVisualLogicGraphRequest,
    VisualLogicNode,
    compile_visual_logic_graph,
    visual_logic_capabilities,
)
from aura_music_studio.game_forge_world import PhysicsBodyDNA, WorldEntityDNA, ensure_world, load_world, save_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="visual-gameplay-user")


def _game(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    game = GameDNA(
        title="Visual Gameplay Logic Test",
        prompt="Runtime-backed typed visual gameplay behavior test",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    world = ensure_world(game)
    entity = WorldEntityDNA(
        id="gameplay_actor",
        name="Gameplay Actor",
        kind="mesh",
        physics=PhysicsBodyDNA(
            mode="kinematic",
            shape="box",
            mass_kg=0.0,
            collision_layer="world",
            collision_mask=["world", "player"],
        ),
    )
    world.entities.append(entity)
    world.touch()
    save_world(world)
    return game, entity.id


def test_visual_logic_gameplay_ops_compile_into_production_runtime_payload(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    graph = compile_visual_logic_graph(
        game,
        entity_id,
        PutVisualLogicGraphRequest(
            nodes=[
                VisualLogicNode(
                    id="collect",
                    op="collectible",
                    params={"points": 9_999_999, "respawn": True, "respawn_seconds": 0, "script": "never"},
                ),
                VisualLogicNode(
                    id="hazard",
                    op="damage",
                    params={"amount": 5000, "reset_to_checkpoint": False, "cooldown_seconds": 0, "url": "https://invalid.example"},
                ),
                VisualLogicNode(id="checkpoint", op="checkpoint", params={"label": "C" * 200}),
                VisualLogicNode(
                    id="patrol",
                    op="patrol",
                    params={"axis": "bad", "distance": -10, "speed": 5000, "ping_pong": False},
                ),
                VisualLogicNode(
                    id="trigger",
                    op="quest_trigger",
                    params={"event": "quest_open", "once": False, "javascript": "alert(1)"},
                ),
            ]
        ),
    )

    by_id = {node.id: node for node in graph.nodes}
    assert by_id["collect"].params == {"points": 1_000_000, "respawn": True, "respawn_seconds": 0.25}
    assert by_id["hazard"].params == {"amount": 1000, "reset_to_checkpoint": False, "cooldown_seconds": 0.05}
    assert by_id["checkpoint"].params == {"label": "C" * 80}
    assert by_id["patrol"].params == {"axis": "x", "distance": 0.0, "speed": 1000.0, "ping_pong": False}
    assert by_id["trigger"].params == {"event": "quest_open", "once": False}
    serialized = str(graph.model_dump()).lower()
    assert "script" not in serialized
    assert "javascript" not in serialized
    assert "invalid.example" not in serialized

    world = load_world(game.id)
    runtime = gameplay_runtime_payload(world)
    row = next(item for item in runtime["entities"] if item["id"] == entity_id)
    assert [behavior["op"] for behavior in row["behaviors"]] == [
        "collectible",
        "damage",
        "checkpoint",
        "patrol",
        "quest_trigger",
    ]
    assert runtime["arbitrary_script_source_allowed"] is False


def test_visual_logic_capabilities_report_only_verified_runtime_operations():
    capabilities = visual_logic_capabilities("game_1")
    assert capabilities["compiler"] == "aura_world_logic.v2"
    assert set(capabilities["world_logic_ops"]) == {"follow_target", "timer", "door"}
    assert set(capabilities["aura3d_gameplay_ops"]) == {
        "collectible",
        "damage",
        "checkpoint",
        "patrol",
        "quest_trigger",
    }
    assert set(capabilities["runtime_ops"]) == set(capabilities["world_logic_ops"]) | set(capabilities["aura3d_gameplay_ops"])
    assert capabilities["arbitrary_script_source_allowed"] is False
    assert capabilities["network_access_from_graph"] is False


def test_visual_logic_collision_gameplay_nodes_require_physics(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == entity_id)
    row.physics = None
    world.touch()
    save_world(world)

    for op in ("collectible", "damage", "checkpoint", "quest_trigger"):
        with pytest.raises(ValueError, match="requires Physics DNA"):
            compile_visual_logic_graph(
                game,
                entity_id,
                PutVisualLogicGraphRequest(nodes=[VisualLogicNode(id=f"node_{op}", op=op)]),
            )


def test_visual_logic_patrol_requires_movable_physics(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == entity_id)
    row.physics.mode = "static"
    world.touch()
    save_world(world)

    with pytest.raises(ValueError, match="patrol requires kinematic or dynamic"):
        compile_visual_logic_graph(
            game,
            entity_id,
            PutVisualLogicGraphRequest(nodes=[VisualLogicNode(id="patrol", op="patrol")]),
        )
