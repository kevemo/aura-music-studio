from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_live_3d import inject_live_3d_tuning
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_native3d import _inject_private_live_player_speed_hook
from aura_music_studio.game_forge_state_machine_runtime import build_state_machine_playtest
from aura_music_studio.game_forge_world import ensure_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="live-3d-user")


def _game(*, dimension: str = "3d") -> GameDNA:
    return GameDNA(
        title="Aura3D Live Tuning",
        prompt="A reversible authored-entity live tuning regression game",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
    )


def _patch_storage(monkeypatch, tmp_path):
    games = tmp_path / "games"
    public = tmp_path / "public"
    games.mkdir()
    public.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: games)
    monkeypatch.setattr(store, "PUBLIC_GAMES_ROOT", public)
    return games


def test_aura3d_live_tuning_is_private_authored_only_and_reversible():
    game = _game()
    html = inject_live_3d_tuning("<!doctype html><html><body></body></html>", game=game)

    assert "id='aura-live-3d-tuning'" in html
    assert f"/api/game-forge/games/${{encodeURIComponent(String(cfg3.game_id||''))}}/playtest-frame" in html
    assert "location.pathname!==expected3" in html
    assert '"max_tunable_entities":32' in html
    assert '"player_speed_min":1.5' in html
    assert '"player_speed_max":14.0' in html
    assert '"player_speed_default":6.0' in html
    assert '"project_persistence":false' in html
    assert '"arbitrary_entity_creation":false' in html
    assert '"authored_entities_only":true' in html

    assert "typeof auraRows==='undefined'" in html
    assert "typeof auraEntity!=='function'" in html
    assert "auraState.get" in html
    assert "behavior3(row,op)" in html
    assert "op=kind==='hazards'?'damage':'collectible'" in html
    assert ".slice(0,max3)" in html

    assert "hidden3.set" in html
    assert "item.state.taken=true" in html
    assert "item.e.scale={x:.000001,y:.000001,z:.000001}" in html
    assert "restoreSnapshot3" in html
    assert "history3.length>20" in html
    assert "Undo 3D" in html
    assert "AuraLive3D=Object.freeze" in html

    # Other runtime layers can update entity transforms, so suppression is reasserted each frame.
    assert "function enforce3()" in html
    assert "for(const [id,saved] of hidden3)" in html
    assert "requestAnimationFrame(enforce3)" in html

    # Movement speed uses the native hook and shares the same 3D undo history.
    assert "window.Aura3DPlayerTuning=movement3" in html
    assert "movement3.speed=clampSpeed3" in html
    assert "function speed3(multiplier,label)" in html
    assert "speed3(1.2,'faster')" in html
    assert "speed3(.82,'slower')" in html
    assert "player_speed_tuning:true" in html
    assert "player_speed_min:minSpeed3" in html
    assert "player_speed_max:maxSpeed3" in html
    assert "speed:clampSpeed3(movement3.speed)" in html

    # More can restore only something this live session previously suppressed.
    assert "Array.from(hidden3.entries())" in html
    assert "All currently available authored ${kind} are already active." in html
    assert "arbitrary_entity_creation:false" in html

    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "eval(" not in html
    assert "new Function" not in html


def test_aura3d_tuning_intercepts_supported_3d_commands_before_base_copilot():
    html = inject_live_3d_tuning("<html><body></body></html>", game=_game())
    assert "document.addEventListener('click'" in html
    assert "document.addEventListener('keydown'" in html
    assert "window.addEventListener('message'" in html
    assert "event.stopImmediatePropagation()" in html
    assert "command3(input3.value)" in html
    assert "command3(data.command)" in html
    assert "undo 3d" in html
    assert "faster|speed up|quicker" in html
    assert "slower|slow down" in html
    assert "harder|more difficult" in html
    assert "easier|less difficult" in html
    assert "hazard|hazards|enemy|enemies|danger" in html
    assert "collectible|collectibles|pickup|pickups" in html


def test_aura3d_voice_interception_revalidates_parent_game_channel_and_bounds():
    html = inject_live_3d_tuning("<html><body></body></html>", game=_game())
    assert "event.source!==window.parent" in html
    assert "data.type!==liveCfg.voice_message_type" in html
    assert "data.game_id!==String(cfg3.game_id)" in html
    assert "data.channel!==channel" in html
    assert "data.command.length>240" in html
    assert "new URLSearchParams(location.search)" in html


def test_aura3d_live_tuning_is_noop_for_2d_and_idempotent_for_3d():
    base = "<html><body></body></html>"
    assert inject_live_3d_tuning(base, game=_game(dimension="2d")) == base
    first = inject_live_3d_tuning(base, game=_game())
    assert inject_live_3d_tuning(first, game=_game()) == first


def test_aura3d_live_tuning_fails_closed_on_changed_document_boundary():
    with pytest.raises(ValueError, match="document boundary changed"):
        inject_live_3d_tuning("<html><body>broken", game=_game())


def test_native_aura3d_speed_hook_is_exact_bounded_and_fail_closed():
    boundary = (
        "before;const len=Math.hypot(dx,dz)||1,speed=6;"
        "player.position.x+=dx/len*speed*dt;"
        "player.position.z-=dz/len*speed*dt;after"
    )
    hooked = _inject_private_live_player_speed_hook(boundary)
    assert "speed=6" not in hooked
    assert "Number(window.Aura3DPlayerTuning?.speed)||6" in hooked
    assert "Math.max(1.5,Math.min(14" in hooked
    assert "player.position.x+=dx/len*speed*dt" in hooked
    assert "player.position.z-=dz/len*speed*dt" in hooked

    with pytest.raises(ValueError, match="movement boundary changed"):
        _inject_private_live_player_speed_hook("<html>renderer changed</html>")
    with pytest.raises(ValueError, match="movement boundary changed"):
        _inject_private_live_player_speed_hook(boundary + boundary)


def test_cumulative_3d_build_includes_copilot_entity_tuning_and_native_speed_hook(monkeypatch, tmp_path):
    games = _patch_storage(monkeypatch, tmp_path)
    game = _game()
    store.create_game(_member(), game)
    ensure_world(game)

    game, html = build_state_machine_playtest(game)

    assert game.latest_build is not None
    assert "id='aura-live-copilot'" in html
    assert "id='aura-live-3d-tuning'" in html
    assert "connect-src 'none'" in html
    assert "window.AuraLive3D=Object.freeze" in html
    assert "arbitrary_entity_creation:false" in html
    assert "project_persistence:false" in html
    assert "Number(window.Aura3DPlayerTuning?.speed)||6" in html
    assert "Math.max(1.5,Math.min(14" in html
    assert "speed=6;player.position.x" not in html
    assert '"private_live_player_speed_hook": true' in html or '"private_live_player_speed_hook":true' in html

    persisted = games / game.id / "builds" / game.latest_build.build_id / "play.html"
    assert persisted.is_file()
    saved = persisted.read_text(encoding="utf-8")
    assert saved == html
    assert "location.pathname!==expected3" in saved
