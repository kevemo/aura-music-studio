from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio import game_forge_store as store
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.game_forge_native3d import render_aura3d_playtest
from aura_music_studio.game_forge_runtime import PLAYTEST_CSP, build_private_playtest
from aura_music_studio.game_forge_world import generate_foundation_world
from aura_music_studio.plans import get_plan


def _member():
    return SimpleNamespace(plan=get_plan("base"), user_id="native3d-user")


def _patch_storage(monkeypatch, tmp_path):
    root = tmp_path / "games"
    root.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)
    import aura_music_studio.game_forge_world as world_module
    import aura_music_studio.game_forge_runtime as runtime_module

    monkeypatch.setattr(world_module, "game_dir", store.game_dir)
    monkeypatch.setattr(runtime_module, "game_dir", store.game_dir)
    monkeypatch.setattr(runtime_module, "save_game", store.save_game)
    return root


def test_native3d_renderer_consumes_world_dna_without_external_engine(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Aura Dragon Valley",
        prompt="A native 3D dragon exploration world",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    world = generate_foundation_world(game)
    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)

    assert "Aura Game Engine 3D" in html
    assert "Aura Game Engine 3D v4" in html
    assert "getContext('webgl2'" in html
    assert "gl.enable(gl.DEPTH_TEST)" in html
    assert "gl.enable(gl.CULL_FACE)" in html
    assert "streaming_cell_size" in html
    assert "lod_distances" in html
    assert "navigator.gpu" in html
    assert '"native_aura_renderer": true' in html
    assert '"external_engine": null' in html
    assert '"runtime_version": 4' in html
    assert '"static_3d_models": true' in html
    assert '"model_runtime_loading": false' in html
    assert '"declarative_cinematics": true' in html
    assert '"creator_javascript": false' in html
    assert '"creator_shader_code": false' in html

    # The playtest CSP/runtime cannot make provider calls or open network sockets.
    assert "connect-src 'none'" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "https://cdn" not in html
    assert "phaser" not in html.lower()
    assert "playcanvas" not in html.lower()
    assert "babylon" not in html.lower()


def test_native3d_creator_text_cannot_break_out_of_config_script(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="</script><script>alert('x')</script>",
        prompt="Safe world",
        dimension="3d",
        engine_target="aura3d",
    )
    store.create_game(_member(), game)
    world = generate_foundation_world(game)
    html = render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)
    assert "</script><script>alert('x')</script>" not in html
    assert "<\\/script>" in html


def test_build_selects_aura3d_v4_runtime_for_native_3d_game(monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    game = GameDNA(
        title="Native 3D",
        prompt="Explore a safe world",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
    )
    store.create_game(_member(), game)
    generate_foundation_world(game)
    game, html = build_private_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_3d_webgl2_v4"
    assert game.latest_build.requested_engine == "aura3d"
    assert "getContext('webgl2'" in html
    assert "Aura Game Engine 3D v4" in html
    assert game.latest_build.network_access_enabled is False
    assert game.latest_build.arbitrary_server_code_executed is False
