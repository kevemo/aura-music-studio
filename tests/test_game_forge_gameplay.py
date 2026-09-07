from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio import aura_agent_tools as aura_tools
from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_gameplay import (
    CreateGameplayEntityRequest,
    create_gameplay_entity,
    gameplay_publication_blockers,
    gameplay_runtime_payload,
)
from aura_music_studio.game_forge_gameplay_runtime import (
    build_gameplay_playtest,
    render_gameplay_playtest,
)
from aura_music_studio.game_forge_integrity import game_integrity_hash
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_world import (
    BehaviorNodeDNA,
    PhysicsBodyDNA,
    TransformDNA,
    Vec3,
    WorldEntityDNA,
    ensure_world,
    load_world,
    save_world,
)
from aura_music_studio.game_forge_world_api import router as world_router
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="gameplay-user")


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
        title="Gameplay DNA",
        prompt="A declarative game used to verify Aura gameplay execution",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    ensure_world(game)
    return game


def test_preset_entity_authors_world_dna_and_changes_integrity(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    before = game_integrity_hash(game)
    entity = create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(
            name="Golden Star",
            preset="collectible",
            position=Vec3(x=3, y=2, z=0),
            scale=Vec3(x=0.8, y=0.8, z=0.8),
        ),
    )
    world = load_world(game.id)
    saved = next(row for row in world.entities if row.id == entity.id)
    assert saved.kind == "collectible"
    assert saved.physics is not None
    assert saved.physics.mode == "trigger"
    assert saved.physics.collision_layer == "collectible"
    assert saved.physics.collision_mask == ["player"]
    assert saved.behaviors[0].op == "collectible"
    assert saved.behaviors[0].params["points"] == 1
    assert game_integrity_hash(game) != before
    assert game.latest_build is None
    assert game.rating_assessment is None
    assert game.status == "draft"


def test_runtime_payload_strips_unknown_behavior_data_and_clamps_values(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    entity = create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(name="Unsafe Input", preset="collectible"),
    )
    world = load_world(game.id)
    saved = next(row for row in world.entities if row.id == entity.id)
    saved.behaviors[0].params = {
        "points": 999_999_999,
        "respawn": "yes",
        "respawn_seconds": 0,
        "script": "alert('must never execute')",
        "url": "https://example.invalid/payload.js",
    }
    save_world(world)

    payload = gameplay_runtime_payload(world)
    row = next(item for item in payload["entities"] if item["id"] == entity.id)
    params = row["behaviors"][0]["params"]
    assert params == {"points": 1_000_000, "respawn": False, "respawn_seconds": 0.25}
    assert "script" not in str(row).lower()
    assert "url" not in str(row).lower()
    assert payload["arbitrary_script_source_allowed"] is False


def test_gameplay_integrity_blockers_reject_collision_without_physics_and_static_patrol(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path, dimension="3d")
    world = load_world(game.id)
    world.entities.append(
        WorldEntityDNA(
            name="Broken Hazard",
            kind="hazard",
            transform=TransformDNA(position=Vec3(x=1, y=1, z=1)),
            behaviors=[BehaviorNodeDNA(op="damage", params={"amount": 1})],
            physics=None,
        )
    )
    world.entities.append(
        WorldEntityDNA(
            name="Frozen Patrol",
            kind="npc",
            transform=TransformDNA(position=Vec3(x=2, y=1, z=1)),
            behaviors=[BehaviorNodeDNA(op="patrol", params={"axis": "x", "distance": 2, "speed": 1})],
            physics=PhysicsBodyDNA(mode="static", shape="capsule"),
        )
    )
    world.touch()
    save_world(world)
    blockers = " ".join(gameplay_publication_blockers(game.id)).lower()
    assert "broken hazard" in blockers
    assert "requires physics dna" in blockers
    assert "frozen patrol" in blockers
    assert "kinematic or dynamic" in blockers


def test_aura2d_playtest_executes_authored_gameplay_without_network_or_generated_code(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(name="Coin", preset="collectible", position=Vec3(x=2, y=1, z=0)),
    )
    create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(name="Lava", preset="hazard", position=Vec3(x=4, y=1, z=0)),
    )
    html = render_gameplay_playtest(game)
    assert "id='aura-gameplay-dna'" in html
    assert "aura-gameplay-overlay" in html
    assert "auraGameplayUpdate" in html
    assert "collectible" in html
    assert "damage" in html
    assert "reset_to_checkpoint" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html

    game, built = build_gameplay_playtest(game)
    assert built == html
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v2_gameplay"
    assert (store.game_dir(game.id) / "builds" / game.latest_build.build_id / "play.html").is_file()


def test_aura3d_playtest_layers_gameplay_over_existing_renderer(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path, dimension="3d")
    create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(name="Moving Bridge", preset="moving_platform", position=Vec3(x=2, y=1, z=0)),
    )
    create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(name="Checkpoint One", preset="checkpoint", position=Vec3(x=4, y=1, z=0)),
    )
    html = render_gameplay_playtest(game)
    assert "id='aura-gameplay-dna'" in html
    assert "aura-gameplay-hud" in html
    assert "auraGameplayUpdate" in html
    assert "auraPatrol" in html
    assert "auraPhysics" in html
    assert "gl.drawArrays" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html

    game, _html = build_gameplay_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v5_gameplay"


def test_gameplay_routes_editor_and_authoritative_build_work_end_to_end(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    client = TestClient(app)

    create = client.post(
        f"/api/game-forge/games/{game.id}/gameplay/entities",
        json={
            "name": "API Coin",
            "preset": "collectible",
            "position": {"x": 1, "y": 2, "z": 0},
            "scale": {"x": 1, "y": 1, "z": 1},
        },
    )
    assert create.status_code == 200
    entity_id = create.json()["entity"]["id"]

    state = client.get(f"/api/game-forge/games/{game.id}/gameplay")
    assert state.status_code == 200
    assert any(row["id"] == entity_id for row in state.json()["entities"])

    editor = client.get(f"/game-creation/gameplay/{game.id}")
    assert editor.status_code == 200
    assert "Gameplay &amp; Physics" in editor.text or "Gameplay & Physics" in editor.text
    assert "Add to World DNA" in editor.text
    assert "arbitrary" in editor.text.lower()

    build = client.post(f"/api/game-forge/games/{game.id}/build")
    assert build.status_code == 200
    body = build.json()
    assert body["declarative_gameplay_runtime"] is True
    assert body["runtime_network_access"] is False
    assert body["runtime"] == "aura_game_runtime_2d_canvas_v3_adventure"
    assert body["adventure_state_runtime"] is True
    assert body["gameplay_editor_url"] == f"/game-creation/gameplay/{game.id}"

    delete = client.delete(f"/api/game-forge/games/{game.id}/gameplay/entities/{entity_id}")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


def test_gameplay_aura_tools_are_registered_without_duplicate_names():
    names = [spec.name for spec in aura_tools.TOOL_SPECS]
    for name in (
        "inspect_game_gameplay",
        "create_gameplay_entity",
        "update_gameplay_entity",
        "apply_gameplay_preset",
        "delete_gameplay_entity",
        "build_gameplay_playtest",
    ):
        assert name in names
    assert len(names) == len(set(names))
