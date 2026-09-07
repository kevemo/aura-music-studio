from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio.game_forge_api import CreateGameRequest, create_game_for_member
from aura_music_studio.game_forge_integrity import game_integrity_hash
from aura_music_studio.game_forge_models import ENGINE_REGISTRY, GameContentDisclosure, GameDNA
from aura_music_studio.game_forge_ratings import assess_game, rating_content_hash
from aura_music_studio.game_forge_runtime import PLAYTEST_CSP, build_private_playtest, render_foundation_playtest
from aura_music_studio import game_forge_store as store
from aura_music_studio.plans import GAME_CREATE, GAME_CREATE_UNLIMITED, GAME_PLAYTEST, get_plan


def _member(plan_id: str):
    return SimpleNamespace(plan=get_plan(plan_id), user_id=f"user-{plan_id}")


def test_game_forge_plan_contract_free_basic_pro():
    free = get_plan("free")
    basic = get_plan("base")
    pro = get_plan("pro")
    assert free.has(GAME_PLAYTEST)
    assert not free.has(GAME_CREATE)
    assert basic.has(GAME_PLAYTEST) and basic.has(GAME_CREATE)
    assert not basic.has(GAME_CREATE_UNLIMITED)
    assert pro.has(GAME_CREATE) and pro.has(GAME_CREATE_UNLIMITED)


def test_aura_native_engines_are_primary_and_external_engines_are_adapters(monkeypatch, tmp_path):
    assert ENGINE_REGISTRY["aura2d"]["native"] is True
    assert ENGINE_REGISTRY["aura3d"]["native"] is True
    for key in ("phaser4", "playcanvas", "babylon", "godot"):
        assert ENGINE_REGISTRY[key]["native"] is False
        assert "adapter" in ENGINE_REGISTRY[key]["role"].lower()

    root = tmp_path / "games"
    root.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)
    game2d = create_game_for_member(
        _member("pro"),
        CreateGameRequest(title="Aura Two", prompt="A 2D platform adventure", dimension="2d", rights_confirmed=True),
    )
    game3d = create_game_for_member(
        _member("pro"),
        CreateGameRequest(title="Aura Three", prompt="A 3D exploration adventure", dimension="3d", rights_confirmed=True),
    )
    assert game2d.engine_target == "aura2d"
    assert game3d.engine_target == "aura3d"
    assert game2d.metadata["aura_native_engine"] is True
    assert game3d.metadata["external_engines_are_export_adapters"] is True


def test_basic_allows_one_active_game_pro_allows_unlimited(monkeypatch, tmp_path):
    root = tmp_path / "games"
    root.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)

    first = GameDNA(title="First", prompt="A platform adventure", rights_confirmed=True)
    store.create_game(_member("base"), first)
    with pytest.raises(PermissionError, match="one active editable game"):
        store.create_game(_member("base"), GameDNA(title="Second", prompt="Another game"))

    first.status = "archived"
    store.save_game(first)
    store.create_game(_member("base"), GameDNA(title="Second", prompt="Another game"))

    store.create_game(_member("pro"), GameDNA(title="Third", prompt="Third game"))
    store.create_game(_member("pro"), GameDNA(title="Fourth", prompt="Fourth game"))
    assert len(store.list_games()) == 4


def test_rating_is_provisional_and_hash_changes_when_material_content_changes():
    game = GameDNA(
        title="Sky Runner",
        prompt="A bright obstacle-course adventure",
        rights_confirmed=True,
        content=GameContentDisclosure(violence="mild"),
    )
    assessment = assess_game(game)
    assert assessment.provisional_only is True
    assert assessment.official_rating is False
    assert assessment.public_test_allowed is True
    assert "not an official ESRB" in assessment.note
    old_hash = assessment.content_hash

    game.content.paid_random_items = True
    changed = assess_game(game)
    assert changed.content_hash != old_hash
    assert changed.suggested_age_floor >= 16
    assert "At least M" in changed.regional_estimates["Australia / ACB-like estimate"]


def test_rating_blocks_public_test_for_gambling_unmoderated_chat_and_missing_rights():
    game = GameDNA(
        title="Risky World",
        prompt="A multiplayer casino-style world",
        rights_confirmed=False,
        content=GameContentDisclosure(
            gambling_simulation="moderate",
            real_money_gambling=True,
            user_chat=True,
            online_multiplayer=True,
            moderation_controls=False,
            report_and_block_controls=False,
        ),
    )
    result = assess_game(game)
    assert result.suggested_age_floor == 18
    assert result.public_test_allowed is False
    joined = " ".join(result.blockers).lower()
    assert "real-money gambling" in joined
    assert "user chat" in joined
    assert "rights" in joined


def test_child_directed_privacy_conflicts_fail_closed():
    game = GameDNA(
        title="Kids World",
        prompt="A child-directed building game",
        rights_confirmed=True,
        content=GameContentDisclosure(
            child_directed=True,
            collects_personal_data=True,
            privacy_policy_ready=False,
            age_assurance_ready=False,
            profiling_ads=True,
        ),
    )
    result = assess_game(game)
    assert not result.public_test_allowed
    text = " ".join(result.blockers).lower()
    assert "privacy policy" in text
    assert "profiling advertising" in text
    assert "age/consent" in text


def test_foundation_playtest_is_curated_no_network_and_not_arbitrary_server_code(monkeypatch, tmp_path):
    root = tmp_path / "games"
    root.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)
    import aura_music_studio.game_forge_runtime as runtime
    monkeypatch.setattr(runtime, "game_dir", store.game_dir)
    monkeypatch.setattr(runtime, "save_game", store.save_game)

    game = GameDNA(title="Cosmic Dash", prompt="Collect stars in space", rights_confirmed=True)
    store.create_game(_member("base"), game)
    game, html = build_private_playtest(game)
    assert game.latest_build is not None
    assert game.latest_build.runtime == "aura_game_runtime_2d_canvas_v1"
    assert game.latest_build.requested_engine == "aura2d"
    assert game.latest_build.arbitrary_server_code_executed is False
    assert game.latest_build.network_access_enabled is False
    assert game.latest_build.content_hash == game_integrity_hash(game)
    assert game.latest_build.content_hash != rating_content_hash(game)
    assert "connect-src 'none'" in PLAYTEST_CSP
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
    assert "WASD / arrows" in html


def test_runtime_escapes_json_script_breakout_from_creator_text(monkeypatch, tmp_path):
    root = tmp_path / "games"
    root.mkdir()
    monkeypatch.setattr(store, "games_root", lambda: root)
    game = GameDNA(title="</script><script>alert(1)</script>", prompt="Safe prototype")
    store.create_game(_member("base"), game)
    html = render_foundation_playtest(game)
    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script>" in html


def test_game_forge_is_mounted_in_production_entrypoint():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "game_forge_api" in source
    assert "game_forge_portal" in source
    assert "install_aura_game_tools" in source
    assert "app.include_router(game_forge_router)" in source
    assert "app.include_router(game_forge_portal_router)" in source
