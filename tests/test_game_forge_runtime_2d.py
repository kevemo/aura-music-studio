from __future__ import annotations

import pytest

import aura_music_studio.game_forge_runtime_2d as runtime_2d
from aura_music_studio.game_forge_world import GameWorldDNA, TransformDNA, Vec3, WorldEntityDNA


def _kernel_html() -> str:
    return """<!doctype html><html><head>
<meta http-equiv='Content-Security-Policy' content="default-src 'none'; connect-src 'none'">
</head><body><canvas id='game'></canvas><script>
'use strict';let W=800,H=600,score=0,lives=3;const p={x:400,y:300,r:18,s:260};function drawPlayer(){}
</script><script id='aura-runtime-state-dna' type='application/json'>{}</script><script>
window.AuraRuntimeState={};
</script></body></html>"""


def _bridge_payload() -> dict:
    return {
        "version": 1,
        "projection": "world_xz_to_canvas_v1",
        "pixels_per_world_unit": 48,
        "entities": [
            {
                "id": "relic",
                "name": "Relic",
                "kind": "collectible",
                "position": {"x": 1, "y": 0, "z": 1},
                "scale": {"x": 1, "y": 1, "z": 1},
                "active": True,
                "visible": True,
                "physics": {"shape": "sphere"},
                "behaviors": [{"id": "b1", "op": "collectible", "params": {"points": 5, "respawn": False, "respawn_seconds": 3}}],
            },
            {
                "id": "hazard",
                "name": "Hazard",
                "kind": "hazard",
                "position": {"x": 2, "y": 0, "z": 0},
                "scale": {"x": 2, "y": 1, "z": 1},
                "active": True,
                "visible": True,
                "physics": {"shape": "box"},
                "behaviors": [{"id": "b2", "op": "damage", "params": {"amount": 1, "reset_to_checkpoint": True, "cooldown_seconds": 0.75}}],
            },
        ],
        "runtime_contract": {
            "world_dna_collision_bridge": True,
            "collectible": True,
            "damage": True,
            "checkpoint": True,
            "quest_trigger": True,
            "patrol": True,
            "dialogue_and_gate_interaction": True,
            "state_machine_player_near": True,
            "save_state_integration": True,
            "creator_javascript": False,
            "external_network_access": False,
        },
    }


def test_payload_adds_plain_world_entities_referenced_by_adventure(monkeypatch: pytest.MonkeyPatch) -> None:
    guide = WorldEntityDNA(
        id="guide",
        name="Guide",
        kind="npc",
        transform=TransformDNA(position=Vec3(x=2, y=0, z=3)),
    )
    world = GameWorldDNA(game_id="game_demo", dimension="2d", entities=[guide])
    monkeypatch.setattr(
        runtime_2d,
        "adventure_runtime_payload",
        lambda _game: {
            "objectives": [],
            "dialogues": [{"trigger_entity_id": "guide"}],
            "gates": [],
        },
    )
    monkeypatch.setattr(runtime_2d, "gameplay_runtime_payload", lambda _world: {"entities": []})
    monkeypatch.setattr(runtime_2d, "state_machine_runtime_payload", lambda _game_id, world=None: {"entities": []})

    game = type("Game", (), {"id": "game_demo"})()
    payload = runtime_2d.aura2d_world_bridge_payload(game, world)  # type: ignore[arg-type]

    assert payload["projection"] == "world_xz_to_canvas_v1"
    assert payload["pixels_per_world_unit"] == 48
    assert payload["entities"] == [
        {
            "id": "guide",
            "name": "Guide",
            "kind": "npc",
            "position": {"x": 2.0, "y": 0.0, "z": 3.0},
            "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
            "active": True,
            "visible": True,
            "physics": None,
            "behaviors": [],
        }
    ]
    assert payload["runtime_contract"]["creator_javascript"] is False
    assert payload["runtime_contract"]["external_network_access"] is False


def test_payload_preserves_sanitized_gameplay_rows_and_state_machine_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    door = WorldEntityDNA(id="door", name="Door", kind="mesh")
    world = GameWorldDNA(game_id="game_demo", dimension="2d", entities=[door])
    gameplay = _bridge_payload()["entities"][:1]
    monkeypatch.setattr(runtime_2d, "adventure_runtime_payload", lambda _game: {"objectives": [], "dialogues": [], "gates": []})
    monkeypatch.setattr(runtime_2d, "gameplay_runtime_payload", lambda _world: {"entities": gameplay})
    monkeypatch.setattr(
        runtime_2d,
        "state_machine_runtime_payload",
        lambda _game_id, world=None: {"entities": [{"id": "door"}]},
    )

    game = type("Game", (), {"id": "game_demo"})()
    payload = runtime_2d.aura2d_world_bridge_payload(game, world)  # type: ignore[arg-type]
    rows = {row["id"]: row for row in payload["entities"]}

    assert rows["relic"]["behaviors"][0]["op"] == "collectible"
    assert rows["door"]["behaviors"] == []


def test_injected_bridge_executes_2d_world_behaviors_and_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_2d, "aura2d_world_bridge_payload", lambda _game, _world: _bridge_payload())
    rendered = runtime_2d.inject_aura2d_world_bridge(
        _kernel_html(),
        game=object(),  # type: ignore[arg-type]
        world=object(),  # type: ignore[arg-type]
    )

    assert "id='aura2d-world-bridge-dna'" in rendered
    assert "world_xz_to_canvas_v1" in rendered
    assert "window.Aura2DWorldBridge=" in rendered
    assert "aura2d_world_behavior_bridge=true" in rendered
    assert "function hitEntity" in rendered
    assert "shape==='box'" in rendered
    assert "behavior.op==='collectible'" in rendered
    assert "behavior.op==='damage'" in rendered
    assert "behavior.op==='checkpoint'" in rendered
    assert "behavior.op==='quest_trigger'" in rendered
    assert "behavior.op==='patrol'" in rendered
    assert "bridge.dispatch('collect',row.id)" in rendered
    assert "bridge.dispatch('reach',row.id)" in rendered
    assert "bridge.importState=raw=>" in rendered
    assert "window.AuraMarkSaveDirty?.()" in rendered
    assert "auraPlayerPosition=playerWorld" in rendered
    assert "event.key.toLowerCase()!=='e'" in rendered
    assert "authored World DNA active" in rendered
    assert "connect-src 'none'" in rendered
    assert "fetch(" not in rendered
    assert "XMLHttpRequest" not in rendered
    assert "WebSocket" not in rendered


def test_bridge_renders_world_entities_before_native_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_2d, "aura2d_world_bridge_payload", lambda _game, _world: _bridge_payload())
    rendered = runtime_2d.inject_aura2d_world_bridge(_kernel_html(), game=object(), world=object())  # type: ignore[arg-type]

    assert "function drawWorld(){for(const row of entities)drawEntity(row)}" in rendered
    assert "drawPlayer=function(){drawWorld();baseDrawPlayer()}" in rendered
    assert "ctx.fillText(String(row.name||'').slice(0,42)" in rendered


def test_bridge_is_idempotent_and_fails_closed_without_required_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_2d, "aura2d_world_bridge_payload", lambda _game, _world: _bridge_payload())
    once = runtime_2d.inject_aura2d_world_bridge(_kernel_html(), game=object(), world=object())  # type: ignore[arg-type]
    twice = runtime_2d.inject_aura2d_world_bridge(once, game=object(), world=object())  # type: ignore[arg-type]

    assert twice == once
    assert twice.count("id='aura2d-world-bridge-dna'") == 1

    with pytest.raises(ValueError, match="runtime-state kernel"):
        runtime_2d.inject_aura2d_world_bridge("<html><body><script>const p={};function drawPlayer(){}</script></body></html>", game=object(), world=object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="native Canvas player runtime"):
        runtime_2d.inject_aura2d_world_bridge("<html><body><script id='aura-runtime-state-dna'></script></body></html>", game=object(), world=object())  # type: ignore[arg-type]
