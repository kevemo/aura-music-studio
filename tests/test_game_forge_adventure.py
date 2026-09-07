from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aura_music_studio import aura_agent_tools as aura_tools
from aura_music_studio import game_forge_store as store
from aura_music_studio.aura_adventure_tools import install_aura_adventure_tools
from aura_music_studio.aura_gameplay_tools import install_aura_gameplay_tools
from aura_music_studio.game_forge_adventure import (
    AdventureStateDNA,
    CreateDialogueRequest,
    CreateGateRequest,
    CreateItemRequest,
    CreateObjectiveRequest,
    add_dialogue,
    add_gate,
    add_item,
    add_objective,
    adventure_reference_blockers,
    adventure_runtime_payload,
    adventure_path,
)
from aura_music_studio.game_forge_adventure_runtime import build_adventure_playtest, render_adventure_playtest
from aura_music_studio.game_forge_gameplay import CreateGameplayEntityRequest, create_gameplay_entity
from aura_music_studio.game_forge_integrity import game_integrity_hash
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_world import Vec3, ensure_world
from aura_music_studio.game_forge_world_api import router as world_router
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="adventure-user")


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
        title="Adventure DNA",
        prompt="A private declarative adventure used to verify trusted Game Forge state",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    ensure_world(game)
    return game


def _trigger(game, name="Ancient Shrine", x=2.0):
    return create_gameplay_entity(
        game,
        CreateGameplayEntityRequest(name=name, preset="trigger", position=Vec3(x=x, y=0, z=0)),
    )


def test_empty_adventure_materialization_does_not_make_first_build_stale(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    assert not adventure_path(game.id).exists()
    before = game_integrity_hash(game)
    game, html = build_adventure_playtest(game)
    assert adventure_path(game.id).is_file()
    assert game.latest_build is not None
    assert game.latest_build.content_hash == before
    assert game.latest_build.content_hash == game_integrity_hash(game)
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v3_adventure"
    assert "aura-adventure-dna" in html


def test_adventure_edits_change_integrity_and_invalidate_previous_build(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    game, _ = build_adventure_playtest(game)
    before = game_integrity_hash(game)
    assert game.latest_build is not None

    key = add_item(game, CreateItemRequest(name="Temple Key", max_stack=1))
    assert key.name == "Temple Key"
    assert game_integrity_hash(game) != before
    assert game.latest_build is None
    assert game.rating_assessment is None
    assert game.status == "draft"


def test_adventure_schema_forbids_executable_or_unknown_fields():
    with pytest.raises(ValidationError):
        AdventureStateDNA.model_validate({"game_id": "game_x", "script": "alert(1)"})
    with pytest.raises(ValidationError):
        CreateObjectiveRequest.model_validate({"title": "Bad", "kind": "reach", "target_entity_id": "x", "javascript": "evil()"})


def test_adventure_reference_blockers_fail_closed_for_missing_world_entities(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    add_objective(game, CreateObjectiveRequest(title="Find missing portal", kind="reach", target_entity_id="missing_portal"))
    add_dialogue(game, CreateDialogueRequest(trigger_entity_id="missing_npc", speaker="Ghost", lines=["Hello"]))
    add_gate(game, CreateGateRequest(trigger_entity_id="missing_gate", door_entity_id="missing_door", label="Lost Gate"))
    text = " ".join(adventure_reference_blockers(game.id)).lower()
    assert "find missing portal" in text
    assert "ghost" in text
    assert "lost gate" in text
    assert "missing" in text


def test_adventure_runtime_payload_is_private_local_and_bounded(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    item = add_item(game, CreateItemRequest(name="Crystal", max_stack=5))
    trigger = _trigger(game)
    add_objective(game, CreateObjectiveRequest(title="Reach shrine", kind="reach", target_entity_id=trigger.id, reward_item_id=item.id))
    payload = adventure_runtime_payload(game)
    assert payload["save_policy"] == {
        "storage": "browser_local_only",
        "server_sync": False,
        "personal_data": False,
        "content_hash_versioned": True,
    }
    assert payload["arbitrary_script_source_allowed"] is False
    assert payload["objectives"][0]["reward_item_id"] == item.id


def test_adventure_runtime_executes_objectives_dialogue_gates_and_local_save_without_network(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    item = add_item(game, CreateItemRequest(name="Moon Key", max_stack=1))
    shrine = _trigger(game, "Moon Shrine", 2)
    gate_trigger = _trigger(game, "Moon Gate Trigger", 4)
    add_objective(game, CreateObjectiveRequest(title="Visit the shrine", kind="reach", target_entity_id=shrine.id, reward_item_id=item.id, completion_flag="shrine_done"))
    add_dialogue(game, CreateDialogueRequest(trigger_entity_id=shrine.id, speaker="Keeper", lines=["The gate remembers your purpose."], completion_flag="keeper_spoken"))
    add_gate(game, CreateGateRequest(trigger_entity_id=gate_trigger.id, label="Moon Gate", requires_item_id=item.id, consume_item=True, open_flag="moon_gate_open"))

    html = render_adventure_playtest(game)
    assert "id='aura-adventure-dna'" in html
    assert "aura-adventure-hud" in html
    assert "aura-dialogue" in html
    assert "localStorage.getItem" in html
    assert "localStorage.setItem" in html
    assert f"aura-game-state:{game.id}:" in html
    assert "advObjectiveEvent" in html
    assert "advOpenDialogue" in html
    assert "advOpenGate" in html
    assert "server_sync" in html
    assert "personal_data" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "eval(" not in html
    assert "new Function" not in html
    assert "http://" not in html
    assert "https://" not in html


def test_adventure_3d_runtime_layers_on_existing_gameplay_renderer(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path, dimension="3d")
    shrine = _trigger(game, "3D Shrine", 3)
    add_objective(game, CreateObjectiveRequest(title="Reach 3D shrine", kind="reach", target_entity_id=shrine.id))
    game, html = build_adventure_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v6_adventure"
    assert "gl.drawArrays" in html
    assert "auraGameplayUpdate" in html
    assert "aura-adventure-dna" in html
    assert game.latest_build.content_hash == game_integrity_hash(game)


def test_adventure_routes_editor_and_authoritative_build(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    trigger = _trigger(game, "API Trigger")
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    client = TestClient(app)

    item = client.post(f"/api/game-forge/games/{game.id}/adventure/items", json={"name": "API Key", "max_stack": 1})
    assert item.status_code == 200
    item_id = item.json()["item"]["id"]
    objective = client.post(
        f"/api/game-forge/games/{game.id}/adventure/objectives",
        json={"title": "API Objective", "kind": "reach", "target_entity_id": trigger.id, "target_count": 1, "reward_item_id": item_id},
    )
    assert objective.status_code == 200
    state = client.get(f"/api/game-forge/games/{game.id}/adventure")
    assert state.status_code == 200
    assert state.json()["runtime"]["save_policy"]["server_sync"] is False

    editor = client.get(f"/game-creation/adventure/{game.id}")
    assert editor.status_code == 200
    assert "Adventure State" in editor.text
    assert "Browser-local play state" in editor.text
    assert "/adventure/objectives" in editor.text

    built = client.post(f"/api/game-forge/games/{game.id}/build")
    assert built.status_code == 200
    body = built.json()
    assert body["adventure_state_runtime"] is True
    assert body["browser_local_save"] is True
    assert body["server_save_sync"] is False
    assert body["runtime"] == "aura_game_runtime_2d_canvas_v3_adventure"
    assert body["adventure_editor_url"] == f"/game-creation/adventure/{game.id}"


def test_aura_adventure_tools_register_without_replacing_gameplay_or_game_tools():
    install_aura_gameplay_tools()
    install_aura_adventure_tools()
    names = {spec.name for spec in aura_tools.TOOL_SPECS}
    for name in {
        "inspect_game_adventure",
        "add_game_inventory_item",
        "add_game_objective",
        "add_game_dialogue",
        "add_game_gate",
        "delete_game_adventure_entry",
        "build_adventure_playtest",
    }:
        assert name in names
    assert "inspect_game_gameplay" in names
