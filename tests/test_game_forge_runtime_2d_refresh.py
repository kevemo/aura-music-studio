from __future__ import annotations

import pytest

import aura_music_studio.game_forge_runtime_2d as runtime_2d
from aura_music_studio.game_forge_world import GameWorldDNA, TransformDNA, Vec3, WorldEntityDNA


def _kernel_html() -> str:
    return """<!doctype html><html><head><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; connect-src 'none'\"></head><body><canvas id='game'></canvas><script>'use strict';let W=800,H=600,score=0,lives=3;const p={x:400,y:300,r:18,s:260};const stars=[],haz=[];function drawPlayer(){}</script><script id='aura-runtime-state-dna' type='application/json'>{}</script><script>window.AuraRuntimeState={};</script></body></html>"""


def test_payload_includes_only_sanitized_or_referenced_world_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    guide = WorldEntityDNA(id="guide", name="Guide", kind="npc", transform=TransformDNA(position=Vec3(x=2, y=0, z=3)))
    hidden = WorldEntityDNA(id="unreferenced", name="Unreferenced", kind="mesh")
    world = GameWorldDNA(game_id="game_demo", dimension="2d", entities=[guide, hidden])
    monkeypatch.setattr(runtime_2d, "adventure_runtime_payload", lambda _game: {"objectives": [], "dialogues": [{"trigger_entity_id": "guide"}], "gates": []})
    monkeypatch.setattr(runtime_2d, "gameplay_runtime_payload", lambda _world: {"entities": [{"id": "relic", "name": "Relic", "kind": "collectible", "position": {"x": 1, "y": 0, "z": 1}, "scale": {"x": 1, "y": 1, "z": 1}, "active": True, "visible": True, "physics": {"shape": "sphere"}, "behaviors": [{"id": "b1", "op": "collectible", "params": {"points": 5}}]}]})
    monkeypatch.setattr(runtime_2d, "state_machine_runtime_payload", lambda _game_id, world=None: {"entities": []})
    game = type("Game", (), {"id": "game_demo"})()

    payload = runtime_2d.aura2d_world_bridge_payload(game, world)  # type: ignore[arg-type]
    rows = {row["id"]: row for row in payload["entities"]}

    assert set(rows) == {"relic", "guide"}
    assert rows["relic"]["behaviors"][0]["op"] == "collectible"
    assert rows["guide"]["behaviors"] == []
    assert payload["runtime_contract"]["creator_javascript"] is False
    assert payload["runtime_contract"]["external_network_access"] is False


def test_bridge_is_closed_idempotent_and_requires_runtime_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_2d, "aura2d_world_bridge_payload", lambda _game, _world: {"version": 1, "projection": "world_xz_to_canvas_v1", "pixels_per_world_unit": 48, "entities": [], "runtime_contract": {"external_network_access": False}})
    once = runtime_2d.inject_aura2d_world_bridge(_kernel_html(), game=object(), world=object())  # type: ignore[arg-type]
    twice = runtime_2d.inject_aura2d_world_bridge(once, game=object(), world=object())  # type: ignore[arg-type]

    assert twice == once
    assert twice.count("id='aura2d-world-bridge-dna'") == 1
    assert "world_xz_to_canvas_v1" in twice
    assert "behavior.op==='collectible'" in twice
    assert "behavior.op==='damage'" in twice
    assert "behavior.op==='checkpoint'" in twice
    assert "behavior.op==='quest_trigger'" in twice
    assert "behavior.op==='patrol'" in twice
    assert "event.key.toLowerCase()!=='e'" in twice
    assert "spawnEntity=entities.find(row=>row.kind==='spawn')" in twice
    assert "connect-src 'none'" in twice
    assert "fetch(" not in twice
    assert "XMLHttpRequest" not in twice
    assert "WebSocket" not in twice

    with pytest.raises(ValueError, match="runtime-state kernel"):
        runtime_2d.inject_aura2d_world_bridge("<html><body><script>const p={};function drawPlayer(){}</script></body></html>", game=object(), world=object())  # type: ignore[arg-type]


def test_game_forge_runtime_mounts_bridge_only_for_aura2d() -> None:
    from pathlib import Path

    source = Path("aura_music_studio/game_forge_runtime.py").read_text(encoding="utf-8")
    assert "from .game_forge_runtime_2d import inject_aura2d_world_bridge" in source
    assert 'if runtime == "aura2d":' in source
    assert "inject_aura2d_world_bridge(html, game=game, world=world)" in source
    assert source.index("inject_runtime_state(html") < source.index("inject_aura2d_world_bridge(html") < source.index("inject_checkpoint_controls(")
