from __future__ import annotations

import pytest

import aura_music_studio.game_forge_runtime_state as runtime_state


def _playtest_html() -> str:
    return """<!doctype html><html><head>
<meta http-equiv='Content-Security-Policy' content="default-src 'none'; connect-src 'none'">
</head><body><div id='media-controls'></div><script>
'use strict';const entities=[];let player={position:{x:0,y:1,z:0}};function cellVisible(_e){return true}
</script></body></html>"""


def _payload() -> dict:
    return {
        "version": 1,
        "adventure": {
            "version": 1,
            "items": [{"id": "item_key", "name": "Key", "max_stack": 9, "consumable": False}],
            "objectives": [
                {
                    "id": "objective_collect",
                    "title": "Collect relic",
                    "kind": "collect",
                    "target_entity_id": "relic",
                    "target_count": 1,
                    "reward_item_id": "item_key",
                    "reward_quantity": 1,
                    "completion_flag": "relic_found",
                }
            ],
            "dialogues": [
                {
                    "id": "dialogue_guide",
                    "trigger_entity_id": "guide",
                    "speaker": "Guide",
                    "lines": ["Welcome"],
                    "choices": [],
                    "once": True,
                    "completion_flag": "guide_met",
                }
            ],
            "gates": [
                {
                    "id": "gate_exit",
                    "trigger_entity_id": "gate_trigger",
                    "door_entity_id": "door",
                    "label": "Exit",
                    "requires_flag": "relic_found",
                    "requires_item_id": None,
                    "consume_item": False,
                    "open_flag": "exit_open",
                }
            ],
            "initial_flags": {"relic_found": False, "guide_met": False, "exit_open": False},
        },
        "gameplay": {
            "version": 1,
            "entities": [
                {
                    "id": "relic",
                    "name": "Relic",
                    "kind": "collectible",
                    "position": {"x": 1, "y": 0, "z": 1},
                    "scale": {"x": 1, "y": 1, "z": 1},
                    "behaviors": [{"id": "b1", "op": "collectible", "params": {"points": 5, "respawn": False, "respawn_seconds": 3}}],
                },
                {
                    "id": "gate_trigger",
                    "name": "Gate Trigger",
                    "kind": "trigger",
                    "position": {"x": 2, "y": 0, "z": 2},
                    "scale": {"x": 1, "y": 1, "z": 1},
                    "behaviors": [{"id": "b2", "op": "quest_trigger", "params": {"event": "exit_open", "once": True}}],
                },
            ],
        },
        "state_machines": {
            "version": 1,
            "entities": [
                {
                    "id": "door",
                    "name": "Door",
                    "kind": "mesh",
                    "position": {"x": 3, "y": 0, "z": 3},
                    "scale": {"x": 1, "y": 1, "z": 1},
                    "machine": {
                        "initial_state": "closed",
                        "states": [
                            {"state": "closed", "label": "Closed", "visible": True, "offset": {"x": 0, "y": 0, "z": 0}, "message": ""},
                            {"state": "open", "label": "Open", "visible": False, "offset": {"x": 0, "y": 0, "z": 0}, "message": "Opened"},
                        ],
                        "transitions": [
                            {"from_state": "closed", "to_state": "open", "trigger": "adventure_flag", "flag": "exit_open", "flag_value": True, "min_state_seconds": 0.1}
                        ],
                    },
                }
            ],
        },
        "runtime_contract": {
            "browser_local_mutable_state": True,
            "save_export_import": True,
            "aura3d_world_behavior_bridge": True,
            "aura2d_state_kernel": True,
            "aura2d_world_behavior_bridge": False,
            "creator_javascript": False,
            "external_network_access": False,
        },
    }


def test_runtime_payload_composes_only_closed_authoring_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_state, "adventure_runtime_payload", lambda _game: {"version": 1, "kind": "adventure"})
    monkeypatch.setattr(runtime_state, "gameplay_runtime_payload", lambda _world: {"version": 1, "kind": "gameplay"})
    monkeypatch.setattr(runtime_state, "state_machine_runtime_payload", lambda _game_id, world=None: {"version": 1, "kind": "machines", "world": world is not None})

    game = type("Game", (), {"id": "game_demo"})()
    world = object()
    payload = runtime_state.runtime_state_payload(game, world)  # type: ignore[arg-type]

    assert payload["adventure"]["kind"] == "adventure"
    assert payload["gameplay"]["kind"] == "gameplay"
    assert payload["state_machines"]["kind"] == "machines"
    assert payload["state_machines"]["world"] is True
    assert payload["runtime_contract"]["save_export_import"] is True
    assert payload["runtime_contract"]["external_network_access"] is False
    assert payload["runtime_contract"]["creator_javascript"] is False


def test_aura3d_runtime_state_executes_closed_gameplay_adventure_and_machines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_state, "runtime_state_payload", lambda _game, _world: _payload())
    rendered = runtime_state.inject_runtime_state(
        _playtest_html(),
        game=object(),  # type: ignore[arg-type]
        world=object(),  # type: ignore[arg-type]
        runtime="aura3d",
    )

    assert "id='aura-runtime-state-dna'" in rendered
    assert "window.AuraRuntimeState=" in rendered
    assert "auraDispatch" in rendered
    assert "auraInventoryAdd" in rendered
    assert "auraSetFlag" in rendered
    assert "auraCompleteObjective" in rendered
    assert "auraShowDialogue" in rendered
    assert "auraGateTry" in rendered
    assert "auraMachineTick" in rendered
    assert "auraMachineTransition" in rendered
    assert "auraGameplayTick" in rendered
    assert "behavior.op==='collectible'" in rendered
    assert "behavior.op==='damage'" in rendered
    assert "behavior.op==='checkpoint'" in rendered
    assert "behavior.op==='quest_trigger'" in rendered
    assert "behavior.op==='patrol'" in rendered
    assert "event.key.toLowerCase()!=='e'" in rendered
    assert "exportState:auraExportState" in rendered
    assert "validateState:auraValidateState" in rendered
    assert "importState:auraImportState" in rendered
    assert "connect-src 'none'" in rendered
    assert "fetch(" not in rendered
    assert "XMLHttpRequest" not in rendered
    assert "WebSocket" not in rendered


def test_runtime_state_save_snapshot_is_bounded_and_reapplies_world_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_state, "runtime_state_payload", lambda _game, _world: _payload())
    rendered = runtime_state.inject_runtime_state(
        _playtest_html(),
        game=object(),  # type: ignore[arg-type]
        world=object(),  # type: ignore[arg-type]
        runtime="aura3d",
    )

    assert "score:auraClamp(auraState.score,0,1000000000)" in rendered
    assert "lives:Math.trunc(auraClamp(auraState.lives,0,99))" in rendered
    assert "elapsed:auraClamp(state.elapsed,0,3600)" in rendered
    assert "auraState.collected=new Set" in rendered
    assert ".slice(0,10000)" in rendered
    assert "auraApplyAll();return true" in rendered
    assert "door._auraGateHidden" in rendered
    assert "live._auraCollectedHidden" in rendered
    assert "live._auraStateHidden" in rendered


def test_aura2d_gets_state_kernel_without_claiming_world_behavior_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload()
    monkeypatch.setattr(runtime_state, "runtime_state_payload", lambda _game, _world: payload)
    rendered = runtime_state.inject_runtime_state(
        _playtest_html(),
        game=object(),  # type: ignore[arg-type]
        world=object(),  # type: ignore[arg-type]
        runtime="aura2d",
    )

    assert 'const auraRuntimeKind="aura2d"' in rendered
    assert '"aura2d_state_kernel":true' in rendered
    assert '"aura2d_world_behavior_bridge":false' in rendered
    assert "if(auraRuntimeKind==='aura3d')return" not in rendered
    assert "if(auraRuntimeKind!=='aura3d')return" in rendered


def test_runtime_state_injection_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_state, "runtime_state_payload", lambda _game, _world: _payload())
    once = runtime_state.inject_runtime_state(_playtest_html(), game=object(), world=object(), runtime="aura3d")  # type: ignore[arg-type]
    twice = runtime_state.inject_runtime_state(once, game=object(), world=object(), runtime="aura3d")  # type: ignore[arg-type]

    assert twice == once
    assert twice.count("id='aura-runtime-state-dna'") == 1


def test_runtime_state_injection_fails_closed_on_invalid_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_state, "runtime_state_payload", lambda _game, _world: _payload())
    with pytest.raises(ValueError, match="closing body"):
        runtime_state.inject_runtime_state("<html><script></script></html>", game=object(), world=object(), runtime="aura3d")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unsupported Game Forge runtime"):
        runtime_state.inject_runtime_state(_playtest_html(), game=object(), world=object(), runtime="unknown")  # type: ignore[arg-type]
