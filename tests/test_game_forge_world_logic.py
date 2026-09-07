from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio import aura_agent_tools as aura_tools
from aura_music_studio import game_forge_store as store
from aura_music_studio.aura_world_logic_tools import install_aura_world_logic_tools
from aura_music_studio.game_forge_integrity import assess_game_integrity, game_integrity_hash
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_world import BehaviorNodeDNA, Vec3, ensure_world, load_world, save_world
from aura_music_studio.game_forge_world_api import router as world_router
from aura_music_studio.game_forge_world_logic import (
    CreateWorldLogicEntityRequest,
    create_world_logic_entity,
    world_logic_publication_blockers,
    world_logic_runtime_payload,
)
from aura_music_studio.game_forge_world_logic_runtime import build_world_logic_playtest, render_world_logic_playtest
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="world-logic-user")


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games, public


def _game(monkeypatch, tmp_path, *, dimension="2d"):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Advanced World Logic DNA",
        prompt="A declarative Game Forge world used to verify safe advanced logic",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    ensure_world(game)
    return game


def test_world_logic_presets_author_closed_world_dna_and_invalidate_build(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    before = game_integrity_hash(game)
    follower = create_world_logic_entity(
        game,
        CreateWorldLogicEntityRequest(name="Aura Guide", preset="npc_follow", position=Vec3(x=4, y=0, z=0)),
    )
    timer = create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Dawn Bell", preset="timed_trigger"))
    door = create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Temple Door", preset="auto_door"))
    world = load_world(game.id)
    by_id = {row.id: row for row in world.entities}
    assert by_id[follower.id].behaviors[0].op == "follow_target"
    assert by_id[follower.id].physics.mode == "kinematic"
    assert by_id[timer.id].behaviors[0].op == "timer"
    assert by_id[timer.id].visible is False
    assert by_id[door.id].behaviors[0].op == "door"
    assert by_id[door.id].physics.mode == "kinematic"
    assert game_integrity_hash(game) != before
    assert game.latest_build is None
    assert game.rating_assessment is None
    assert game.status == "draft"


def test_world_logic_runtime_sanitizes_params_and_strips_unknown_data(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    follower = create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Guide", preset="npc_follow"))
    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == follower.id)
    row.behaviors[0].params = {
        "target": "player",
        "speed": 99999999,
        "stop_distance": -50,
        "script": "alert('never')",
        "url": "https://example.invalid/evil.js",
    }
    save_world(world)
    payload = world_logic_runtime_payload(world)
    runtime_row = next(item for item in payload["entities"] if item["id"] == follower.id)
    assert runtime_row["behaviors"][0]["params"] == {"target": "player", "speed": 1000.0, "stop_distance": 0.0}
    assert "script" not in str(runtime_row).lower()
    assert "url" not in str(runtime_row).lower()
    assert payload["arbitrary_script_source_allowed"] is False


def test_world_logic_publication_blockers_fail_closed(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    follower = create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Lost Guide", preset="npc_follow"))
    door = create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Broken Door", preset="auto_door"))
    world = load_world(game.id)
    follow_row = next(item for item in world.entities if item.id == follower.id)
    follow_row.behaviors[0].params["target"] = "missing_target"
    follow_row.physics = None
    door_row = next(item for item in world.entities if item.id == door.id)
    door_row.physics.mode = "static"
    world.touch()
    save_world(world)
    blockers = " ".join(world_logic_publication_blockers(game.id)).lower()
    assert "missing_target" in blockers
    assert "lost guide" in blockers
    assert "kinematic or dynamic" in blockers
    assert "broken door" in blockers
    assert "kinematic physics" in blockers
    assessment = assess_game_integrity(game)
    assert assessment.public_test_allowed is False
    assert any("missing_target" in blocker for blocker in assessment.blockers)


def test_world_logic_builder_preserves_prior_runtime_when_no_logic_exists(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    game, html = build_world_logic_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v3_adventure"
    assert "aura-world-logic-dna" not in html
    assert "aura-adventure-dna" in html


def test_world_logic_2d_runtime_layers_over_adventure_and_gameplay_without_network(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Guide", preset="npc_follow", position=Vec3(x=5, y=0, z=0)))
    create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Bell", preset="timed_trigger"))
    create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="Door", preset="auto_door", position=Vec3(x=2, y=0, z=0)))
    html = render_world_logic_playtest(game)
    assert "id='aura-world-logic-dna'" in html
    assert "aura-world-logic-overlay" in html
    assert "follow_target" in html
    assert "logicMoveToward" in html
    assert "logicDoor" in html
    assert "aura-adventure-dna" in html
    assert "auraGameplayUpdate" in html
    assert "connect-src 'none'" in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "eval(" not in html
    assert "new Function" not in html
    game, built = build_world_logic_playtest(game)
    assert built == html
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v4_world_logic"
    assert game.latest_build.content_hash == game_integrity_hash(game)


def test_world_logic_3d_runtime_layers_over_native_renderer(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path, dimension="3d")
    create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="3D Guide", preset="npc_follow", position=Vec3(x=3, y=0, z=2)))
    create_world_logic_entity(game, CreateWorldLogicEntityRequest(name="3D Door", preset="auto_door", position=Vec3(x=1, y=0, z=1)))
    game, html = build_world_logic_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v7_world_logic"
    assert "gl.drawArrays" in html
    assert "aura-world-logic-dna" in html
    assert "logicMoveToward" in html
    assert "logicDoor" in html
    assert "connect-src 'none'" in html


def test_world_logic_routes_workspace_and_authoritative_build(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    client = TestClient(app)
    created = client.post(
        f"/api/game-forge/games/{game.id}/world-logic/entities",
        json={"name": "API Guide", "preset": "npc_follow", "position": {"x": 3, "y": 0, "z": 0}, "scale": {"x": 1, "y": 1, "z": 1}},
    )
    assert created.status_code == 200
    assert created.json()["entity"]["behaviors"][0]["op"] == "follow_target"
    state = client.get(f"/api/game-forge/games/{game.id}/world-logic")
    assert state.status_code == 200
    assert state.json()["runtime"]["arbitrary_script_source_allowed"] is False
    editor = client.get(f"/game-creation/world-logic/{game.id}")
    assert editor.status_code == 200
    assert "Advanced World Logic" in editor.text
    assert "NPC Follow / Escort" in editor.text
    assert "/world-logic/entities" in editor.text
    built = client.post(f"/api/game-forge/games/{game.id}/build")
    assert built.status_code == 200
    body = built.json()
    assert body["advanced_world_logic_runtime"] is True
    assert body["adventure_state_runtime"] is True
    assert body["declarative_gameplay_runtime"] is True
    assert body["runtime_network_access"] is False
    assert body["runtime"] == "aura_game_runtime_2d_canvas_v4_world_logic"
    assert body["world_logic_editor_url"] == f"/game-creation/world-logic/{game.id}"


def test_aura_world_logic_tools_register_without_duplicate_names():
    install_aura_world_logic_tools()
    names = [spec.name for spec in aura_tools.TOOL_SPECS]
    for name in (
        "inspect_game_world_logic",
        "create_game_world_logic_entity",
        "apply_game_world_logic_preset",
        "build_world_logic_playtest",
    ):
        assert name in names
    assert len(names) == len(set(names))
