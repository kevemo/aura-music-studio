from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_models import GameBuild, GameDNA, GameRatingAssessment
from aura_music_studio.game_forge_visual_logic import (
    PutVisualLogicGraphRequest,
    VisualLogicConflict,
    VisualLogicEdge,
    VisualLogicNode,
    compile_visual_logic_graph,
    delete_visual_logic_graph,
    load_visual_logic_graph,
    visual_logic_capabilities,
)
from aura_music_studio.game_forge_world import (
    BehaviorNodeDNA,
    PhysicsBodyDNA,
    WorldEntityDNA,
    ensure_world,
    load_world,
    save_world,
)
from aura_music_studio.game_forge_world_logic import world_logic_runtime_payload
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="visual-logic-user")


def _game(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    game = GameDNA(
        title="Visual Logic Test",
        prompt="A safe declarative Game Forge visual-logic compiler test",
        dimension="2d",
        engine_target="aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    world = ensure_world(game)
    entity = WorldEntityDNA(
        id="logic_actor",
        name="Logic Actor",
        kind="npc",
        physics=PhysicsBodyDNA(
            mode="kinematic",
            shape="box",
            mass_kg=0.0,
            collision_layer="npc",
            collision_mask=["world", "player"],
        ),
        behaviors=[BehaviorNodeDNA(id="manual_timer", op="timer", params={"seconds": 2.0, "event": "Manual"})],
    )
    world.entities.append(entity)
    world.touch()
    save_world(world)
    return game, entity.id


def test_visual_logic_compiles_to_real_world_logic_and_sanitizes_params(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    body = PutVisualLogicGraphRequest(
        nodes=[
            VisualLogicNode(
                id="door",
                op="door",
                params={
                    "axis": "bad-axis",
                    "distance": -5,
                    "speed": 99999,
                    "trigger_distance": 0,
                    "auto_close": True,
                    "close_delay": 99999,
                    "script": "alert('never')",
                },
                canvas_x=420,
                canvas_y=120,
            ),
            VisualLogicNode(
                id="timer",
                op="timer",
                params={
                    "seconds": 999999,
                    "repeat": True,
                    "event": "Open the gate",
                    "url": "https://example.invalid/logic.js",
                },
                canvas_x=80,
                canvas_y=120,
            ),
        ],
        edges=[VisualLogicEdge(source="timer", target="door")],
    )

    graph = compile_visual_logic_graph(game, entity_id, body)
    assert graph.revision == 1
    assert [node.id for node in graph.nodes] == ["timer", "door"]
    assert graph.edges[0].source == "timer"
    assert graph.nodes[0].params == {"seconds": 86400.0, "repeat": True, "event": "Open the gate"}
    assert graph.nodes[1].params == {
        "axis": "y",
        "distance": 0.0,
        "speed": 1000.0,
        "trigger_distance": 0.1,
        "auto_close": True,
        "close_delay": 3600.0,
    }
    assert "script" not in str(graph.model_dump()).lower()
    assert "example.invalid" not in str(graph.model_dump()).lower()

    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == entity_id)
    # A manually authored behavior remains in place; graph-owned behaviors compile after it.
    assert row.behaviors[0].id == "manual_timer"
    assert [item.op for item in row.behaviors[1:]] == ["timer", "door"]
    runtime = world_logic_runtime_payload(world)
    runtime_row = next(item for item in runtime["entities"] if item["id"] == entity_id)
    assert [item["op"] for item in runtime_row["behaviors"]] == ["timer", "timer", "door"]
    assert runtime["arbitrary_script_source_allowed"] is False


def test_visual_logic_rejects_cycles_dangling_edges_and_bad_node_ids(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    nodes = [
        VisualLogicNode(id="a", op="timer", params={"seconds": 1}),
        VisualLogicNode(id="b", op="timer", params={"seconds": 2}),
    ]
    with pytest.raises(ValueError, match="cycle"):
        compile_visual_logic_graph(
            game,
            entity_id,
            PutVisualLogicGraphRequest(
                nodes=nodes,
                edges=[VisualLogicEdge(source="a", target="b"), VisualLogicEdge(source="b", target="a")],
            ),
        )
    with pytest.raises(ValueError, match="connect nodes in this graph"):
        compile_visual_logic_graph(
            game,
            entity_id,
            PutVisualLogicGraphRequest(
                nodes=nodes,
                edges=[VisualLogicEdge(source="a", target="missing")],
            ),
        )
    with pytest.raises(ValueError, match="letters, numbers"):
        compile_visual_logic_graph(
            game,
            entity_id,
            PutVisualLogicGraphRequest(nodes=[VisualLogicNode(id="../../escape", op="timer")]),
        )
    assert load_visual_logic_graph(game.id, entity_id) is None


def test_visual_logic_optimistic_concurrency_blocks_stale_overwrite(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    first = compile_visual_logic_graph(
        game,
        entity_id,
        PutVisualLogicGraphRequest(nodes=[VisualLogicNode(id="timer", op="timer", params={"seconds": 1})]),
    )
    second = compile_visual_logic_graph(
        game,
        entity_id,
        PutVisualLogicGraphRequest(
            nodes=[VisualLogicNode(id="timer", op="timer", params={"seconds": 3})],
            expected_revision=first.revision,
        ),
    )
    assert second.revision == 2
    assert second.nodes[0].params["seconds"] == 3.0
    with pytest.raises(VisualLogicConflict, match="Stale Visual Logic revision"):
        compile_visual_logic_graph(
            game,
            entity_id,
            PutVisualLogicGraphRequest(
                nodes=[VisualLogicNode(id="timer", op="timer", params={"seconds": 8})],
                expected_revision=first.revision,
            ),
        )
    persisted = load_visual_logic_graph(game.id, entity_id)
    assert persisted is not None
    assert persisted.revision == 2
    assert persisted.nodes[0].params["seconds"] == 3.0


def test_visual_logic_runtime_requirements_fail_closed_without_mutating_world(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == entity_id)
    row.physics.mode = "static"
    world.touch()
    save_world(world)
    before = load_world(game.id).model_dump(mode="json")

    with pytest.raises(ValueError, match="kinematic Physics DNA"):
        compile_visual_logic_graph(
            game,
            entity_id,
            PutVisualLogicGraphRequest(nodes=[VisualLogicNode(id="door", op="door")]),
        )
    after = load_world(game.id).model_dump(mode="json")
    assert after == before
    assert load_visual_logic_graph(game.id, entity_id) is None


def test_visual_logic_compile_invalidates_stale_build_rating_and_public_state(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    game.latest_build = GameBuild(content_hash="old-hash", requested_engine="aura2d")
    game.rating_assessment = GameRatingAssessment(content_hash="old-hash")
    game.status = "review_ready"
    game.public_id = "old-public-id"
    store.save_game(game)

    compile_visual_logic_graph(
        game,
        entity_id,
        PutVisualLogicGraphRequest(nodes=[VisualLogicNode(id="timer", op="timer", params={"seconds": 4})]),
    )
    saved = store.load_game(game.id)
    assert saved.latest_build is None
    assert saved.rating_assessment is None
    assert saved.public_id is None
    assert saved.status == "draft"


def test_visual_logic_delete_removes_only_graph_owned_behaviors(monkeypatch, tmp_path):
    game, entity_id = _game(monkeypatch, tmp_path)
    graph = compile_visual_logic_graph(
        game,
        entity_id,
        PutVisualLogicGraphRequest(
            nodes=[
                VisualLogicNode(id="timer", op="timer", params={"seconds": 1}),
                VisualLogicNode(id="door", op="door"),
            ]
        ),
    )
    assert len(graph.compiled_behavior_ids) == 2
    assert delete_visual_logic_graph(game, entity_id) is True
    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == entity_id)
    assert [item.id for item in row.behaviors] == ["manual_timer"]
    assert load_visual_logic_graph(game.id, entity_id) is None
    assert delete_visual_logic_graph(game, entity_id) is False


def test_visual_logic_capability_contract_is_truthful():
    caps = visual_logic_capabilities("game_1")
    assert caps["runtime_ops"] == [
        "checkpoint",
        "collectible",
        "damage",
        "door",
        "follow_target",
        "patrol",
        "quest_trigger",
        "timer",
    ]
    assert caps["edge_semantics"] == "compile_order_only"
    assert caps["compiled_target"] == "WorldEntityDNA.behaviors"
    assert caps["arbitrary_script_source_allowed"] is False
    assert caps["eval_allowed"] is False
    assert caps["network_access_from_graph"] is False
    assert caps["unknown_node_execution"] is False


def test_visual_logic_routes_are_mounted_on_release_app():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        paths = production_entrypoint.app.openapi().get("paths", {})
        required = {
            ("get", "/api/game-forge/games/{game_id}/visual-logic"),
            ("get", "/api/game-forge/games/{game_id}/visual-logic/{entity_id}"),
            ("put", "/api/game-forge/games/{game_id}/visual-logic/{entity_id}"),
            ("delete", "/api/game-forge/games/{game_id}/visual-logic/{entity_id}"),
        }
        missing = sorted((method, path) for method, path in required if method not in paths.get(path, {}))
        if missing:
            raise SystemExit(f"Game Forge Visual Logic routes are missing: {missing}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
