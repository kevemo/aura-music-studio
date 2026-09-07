from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aura_music_studio import aura_agent_tools as aura_tools
from aura_music_studio import game_forge_store as store
from aura_music_studio.aura_state_machine_tools import install_aura_state_machine_tools
from aura_music_studio.game_forge_adventure import AdventureStateDNA, save_adventure
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_state_machine import (
    CreateStateMachineEntityRequest,
    MachineStateDNA,
    MachineTransitionDNA,
    StateMachineDNA,
    StateOffsetDNA,
    create_state_machine_entity,
    state_machine_publication_blockers,
    state_machine_runtime_payload,
    validate_machine_for_game,
)
from aura_music_studio.game_forge_state_machine_runtime import build_state_machine_playtest, render_state_machine_playtest
from aura_music_studio.game_forge_world import BehaviorNodeDNA, ensure_world, load_world, save_world
from aura_music_studio.game_forge_world_api import router as world_router
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="state-machine-user")


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
        title="State Machine DNA",
        prompt="A deterministic no-code state graph regression game",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    ensure_world(game)
    return game


def _timed_machine() -> StateMachineDNA:
    return StateMachineDNA(
        initial_state="idle",
        states=[
            MachineStateDNA(state="idle", label="Idle"),
            MachineStateDNA(state="active", label="Active", offset=StateOffsetDNA(y=1), message="Activated"),
        ],
        transitions=[
            MachineTransitionDNA(from_state="idle", to_state="active", trigger="timer", seconds=1),
            MachineTransitionDNA(from_state="active", to_state="idle", trigger="timer", seconds=1),
        ],
    )


def test_state_machine_schema_rejects_unknown_code_and_invalid_graphs():
    with pytest.raises(ValidationError):
        StateMachineDNA.model_validate({
            "initial_state": "idle",
            "states": [{"state": "idle", "label": "Idle", "script": "alert(1)"}],
            "transitions": [],
        })
    with pytest.raises(ValidationError):
        StateMachineDNA.model_validate({
            "initial_state": "missing",
            "states": [{"state": "idle", "label": "Idle"}],
            "transitions": [],
        })
    with pytest.raises(ValidationError):
        MachineTransitionDNA.model_validate({"from_state": "a", "to_state": "b", "trigger": "timer"})


def test_adventure_flag_transitions_must_reference_declared_flags(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    save_adventure(AdventureStateDNA(game_id=game.id, initial_flags={"vault_open": False}))
    valid = StateMachineDNA(
        initial_state="locked",
        states=[MachineStateDNA(state="locked", label="Locked"), MachineStateDNA(state="open", label="Open")],
        transitions=[MachineTransitionDNA(from_state="locked", to_state="open", trigger="adventure_flag", flag="vault_open")],
    )
    validate_machine_for_game(game.id, valid)
    invalid = valid.model_copy(deep=True)
    invalid.transitions[0].flag = "undeclared_flag"
    with pytest.raises(ValueError, match="undeclared Adventure flag"):
        validate_machine_for_game(game.id, invalid)


def test_state_machine_authoring_invalidates_build_and_is_runtime_bounded(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    game, _ = build_state_machine_playtest(game)
    assert game.latest_build is not None
    assert "state_machine" not in game.latest_build.runtime

    actor = create_state_machine_entity(
        game,
        CreateStateMachineEntityRequest(name="Clockwork Actor", position=StateOffsetDNA(x=2), machine=_timed_machine()),
    )
    assert actor.metadata["aura_state_machine_actor"] is True
    assert game.latest_build is None
    payload = state_machine_runtime_payload(game.id)
    assert payload["max_states_per_machine"] == 16
    assert payload["max_transitions_per_machine"] == 64
    assert payload["max_transitions_per_frame"] == 1
    assert payload["adventure_flags_only"] is True
    assert payload["arbitrary_script_source_allowed"] is False


def test_state_machine_publication_blocks_transform_conflicts(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    actor = create_state_machine_entity(
        game,
        CreateStateMachineEntityRequest(name="Conflicted Actor", machine=_timed_machine()),
    )
    world = load_world(game.id)
    row = next(item for item in world.entities if item.id == actor.id)
    row.behaviors.append(BehaviorNodeDNA(op="patrol", params={"axis": "x", "distance": 2, "speed": 1}))
    world.touch()
    save_world(world)
    text = " ".join(state_machine_publication_blockers(game.id)).lower()
    assert "conflicts" in text
    assert "patrol" in text


def test_state_machine_runtime_is_progressive_and_preserves_no_network_contract(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    game, baseline_html = build_state_machine_playtest(game)
    baseline_runtime = game.latest_build.runtime
    assert "state_machine" not in baseline_runtime
    assert "aura-state-machine-dna" not in baseline_html

    create_state_machine_entity(game, CreateStateMachineEntityRequest(name="Timed Actor", machine=_timed_machine()))
    game, html = build_state_machine_playtest(game)
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v6_state_machine"
    assert "id='aura-state-machine-dna'" in html
    assert "machineUpdate" in html
    assert "max_transitions_per_frame" in html
    assert "connect-src 'none'" in html
    assert "eval(" not in html
    assert "new Function" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html


def test_state_machine_3d_runtime_layers_on_world_events_and_native_renderer(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path, dimension="3d")
    create_state_machine_entity(game, CreateStateMachineEntityRequest(name="3D Actor", position=StateOffsetDNA(y=1), machine=_timed_machine()))
    game, html = build_state_machine_playtest(game)
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v9_state_machine"
    assert "gl.drawArrays" in html
    assert "aura-state-machine-dna" in html
    assert "machineApply" in html
    assert "aura-world-events" not in game.latest_build.runtime


def test_state_machine_routes_workspace_and_authoritative_build(monkeypatch, tmp_path):
    game = _game(monkeypatch, tmp_path)
    app = FastAPI()

    @app.middleware("http")
    async def inject_member(request: Request, call_next):
        request.state.member = _member()
        return await call_next(request)

    app.include_router(world_router)
    client = TestClient(app)
    machine = _timed_machine().model_dump(mode="json")
    created = client.post(
        f"/api/game-forge/games/{game.id}/state-machines/entities",
        json={"name": "API Actor", "position": {"x": 1, "y": 0, "z": 0}, "machine": machine},
    )
    assert created.status_code == 200
    actor_id = created.json()["entity"]["id"]

    state = client.get(f"/api/game-forge/games/{game.id}/state-machines")
    assert state.status_code == 200
    assert state.json()["limits"]["transitions_per_frame"] == 1
    assert any(row["id"] == actor_id for row in state.json()["entities"])

    editor = client.get(f"/game-creation/state-machines/{game.id}")
    assert editor.status_code == 200
    assert "State Machines" in editor.text
    assert "one transition" in editor.text.lower()
    assert "/state-machines/entities" in editor.text

    built = client.post(f"/api/game-forge/games/{game.id}/build")
    assert built.status_code == 200
    body = built.json()
    assert body["state_machine_runtime"] is True
    assert body["max_state_machine_transitions_per_frame"] == 1
    assert body["runtime"] == "aura_game_runtime_2d_canvas_v6_state_machine"
    assert body["state_machine_editor_url"] == f"/game-creation/state-machines/{game.id}"
    assert body["runtime_network_access"] is False


def test_aura_state_machine_tools_register_without_duplicate_names():
    install_aura_state_machine_tools()
    names = [spec.name for spec in aura_tools.TOOL_SPECS]
    for name in (
        "inspect_game_state_machines",
        "create_game_state_machine",
        "replace_game_state_machine",
        "delete_game_state_machine",
        "build_state_machine_playtest",
    ):
        assert name in names
    assert len(names) == len(set(names))
