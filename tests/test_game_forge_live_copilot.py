from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_live_copilot import inject_live_copilot
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_state_machine_runtime import build_state_machine_playtest
from aura_music_studio.game_forge_world import ensure_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="live-copilot-user")


def _game(*, dimension: str = "2d") -> GameDNA:
    return GameDNA(
        title="Aura Live Creation",
        prompt="A live mutable playtest regression game",
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


def test_live_copilot_is_bounded_reversible_and_private_frame_gated():
    game = _game()
    base = "<!doctype html><html><body><canvas id='game'></canvas></body></html>"
    html = inject_live_copilot(base, game=game)

    assert html.count("id='aura-live-copilot'") == 1
    assert "/api/game-forge/games/${encodeURIComponent(String(liveCfg.game_id||''))}/playtest-frame" in html
    assert "location.pathname!==liveExpectedPath" in html
    assert "data-aura-live-creator='1'" in html
    assert "display:none" in html

    assert "window.AuraLiveCreation=Object.freeze" in html
    assert "project_persistence:false" in html
    assert "external_network_access:false" in html
    assert "arbitrary_code_execution:false" in html
    assert "Math.min(20" in html
    assert "Math.min(32" in html
    assert "Math.min(240" in html
    assert "liveUndoLast" in html

    assert "SpeechRecognition" in html
    assert "make it stormy" in html
    assert "liveSetTheme('storm')" in html
    assert "liveAdjustObjects('hazards',3)" in html
    assert "liveAdjustSpeed(1.2,'faster')" in html

    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "eval(" not in html
    assert "new Function" not in html

    assert inject_live_copilot(html, game=game) == html


def test_live_copilot_injection_fails_closed_when_runtime_boundary_changes():
    with pytest.raises(ValueError, match="document boundary changed"):
        inject_live_copilot("<html><body>broken", game=_game())


def test_cumulative_build_mounts_live_copilot_even_without_state_machines(monkeypatch, tmp_path):
    games = _patch_storage(monkeypatch, tmp_path)
    game = _game()
    store.create_game(_member(), game)
    ensure_world(game)

    game, html = build_state_machine_playtest(game)

    assert game.latest_build is not None
    assert "state_machine" not in game.latest_build.runtime
    assert "id='aura-state-machine-dna'" not in html
    assert "id='aura-live-copilot'" in html
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html

    persisted = games / game.id / "builds" / game.latest_build.build_id / "play.html"
    assert persisted.is_file()
    saved_html = persisted.read_text(encoding="utf-8")
    assert saved_html == html
    assert "location.pathname!==liveExpectedPath" in saved_html


def test_live_copilot_3d_contract_keeps_gameplay_mutations_runtime_bounded():
    game = _game(dimension="3d")
    html = inject_live_copilot("<html><body></body></html>", game=game)

    assert '"runtime":"aura3d"' in html
    assert "Dynamic pickup/hazard tuning is available in Aura2D playtests." in html
    assert "Player-speed live tuning is available in Aura2D playtests." in html
    assert "liveSetTheme('night')" in html
    assert "liveSetTheme('neon')" in html
