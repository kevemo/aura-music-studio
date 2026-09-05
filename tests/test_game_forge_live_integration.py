from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.game_forge_live_integration as live
from aura_music_studio.game_forge_models import GameBuild, GameDNA


def _game(*, version: int = 1, rights: bool = True) -> GameDNA:
    return GameDNA(
        id="game_live_test",
        title="Safe Live Game",
        prompt="Build a safe playable test game.",
        dimension="2d",
        engine_target="aura2d",
        rights_confirmed=rights,
        rights_attestation="Creator confirmed rights." if rights else "",
        version=version,
        latest_build=GameBuild(
            build_id=f"build_v{version}",
            content_hash=f"hash_v{version}",
            requested_engine="aura2d",
        ),
    )


def _request(user_id: str = "creator_live"):
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(user_id=user_id)),
        headers={},
    )


def _install(monkeypatch, tmp_path, game: GameDNA):
    member = SimpleNamespace(user_id="creator_live")
    monkeypatch.setattr(live, "_creator", lambda _request: member)
    monkeypatch.setattr(live, "load_game", lambda game_id: game if game_id == game.id else (_ for _ in ()).throw(FileNotFoundError(game_id)))
    monkeypatch.setattr(live, "game_dir", lambda game_id: tmp_path / game_id)
    (tmp_path / game.id).mkdir(parents=True, exist_ok=True)
    return member


def _attach(game: GameDNA, request=None, **overrides):
    body = live.AttachLiveSourceRequest(
        live_session_id=overrides.pop("live_session_id", "live_session_1"),
        **overrides,
    )
    return live.attach_game_live_source(game.id, body, request or _request())


def test_clean_game_source_is_build_pinned_and_privacy_allowlisted(monkeypatch, tmp_path):
    game = _game(version=3)
    _install(monkeypatch, tmp_path, game)

    payload = _attach(game)
    source = payload["source"]
    compat = payload["shared_sky_compatibility"]

    assert payload["idempotent_replay"] is False
    assert source["source_type"] == "clean_game_output"
    assert source["build_id"] == "build_v3"
    assert source["project_version"] == 3
    assert source["project_visibility"] == "private"
    assert source["audience_visibility"] == "private"
    assert source["inclusion_manifest"]["capture_scope"] == "game_runtime"
    assert source["inclusion_manifest"]["approved_surfaces"] == ["game_canvas"]
    assert source["inclusion_manifest"]["whole_window_capture"] is False
    assert source["inclusion_manifest"]["source_code_payload_included"] is False
    assert source["inclusion_manifest"]["credentials_included"] is False
    assert compat["source_type"] == "game_forge"
    assert compat["config"]["build_id"] == "build_v3"
    assert "api_keys_tokens_and_environment_variables" in compat["config"]["exclusion_policy"]
    assert payload["project_privacy_changed"] is False
    assert payload["transport_owned_by_chat_2"] is True
    assert payload["composition_owned_by_chat_3"] is True

    stored = json.loads((tmp_path / game.id / "live" / "shared_sky.json").read_text(encoding="utf-8"))
    assert source["source_adapter_id"] in stored["sources"]


def test_editor_code_and_profiler_sources_require_opaque_explicit_safe_surface(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)

    with pytest.raises(HTTPException) as missing:
        _attach(game, source_type="coding_tutorial")
    assert missing.value.status_code == 409
    assert missing.value.detail["code"] == "live_source_privacy_blocked"

    with pytest.raises(HTTPException) as unsafe:
        _attach(game, source_type="coding_tutorial", presentation_surface_ref="../../private/.env")
    assert unsafe.value.status_code == 400
    assert unsafe.value.detail["code"] == "live_source_privacy_blocked"

    payload = _attach(
        game,
        source_type="coding_tutorial",
        presentation_surface_ref="safe_surface:code_tutorial_1",
        presentation_mode="tutorial",
    )
    manifest = payload["source"]["inclusion_manifest"]
    assert manifest["capture_scope"] == "approved_presentation_surface"
    assert manifest["approved_surfaces"] == ["safe_surface:code_tutorial_1"]
    assert manifest["whole_window_capture"] is False


def test_public_live_source_requires_rights_but_private_development_can_remain_unverified(monkeypatch, tmp_path):
    game = _game(rights=False)
    _install(monkeypatch, tmp_path, game)

    with pytest.raises(HTTPException) as blocked:
        _attach(game, audience_visibility="public")
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "rights_not_verified"

    private = _attach(game, audience_visibility="private")
    assert private["source"]["rights_readiness"] == "unverified"
    assert private["source"]["audience_visibility"] == "private"


def test_attach_is_idempotent_and_working_edits_do_not_silently_change_live_pin(monkeypatch, tmp_path):
    game = _game(version=1)
    _install(monkeypatch, tmp_path, game)

    first = _attach(game)
    source_id = first["source"]["source_adapter_id"]
    assert first["source"]["project_version"] == 1
    assert first["source"]["build_id"] == "build_v1"

    game.version = 2
    game.latest_build = GameBuild(build_id="build_v2", content_hash="hash_v2", requested_engine="aura2d")

    replay = _attach(game)
    assert replay["idempotent_replay"] is True
    assert replay["source"]["source_adapter_id"] == source_id
    assert replay["source"]["project_version"] == 1
    assert replay["source"]["build_id"] == "build_v1"

    promoted = live.promote_game_live_version(
        game.id,
        source_id,
        live.PromoteLiveVersionRequest(expected_project_version=2, expected_build_id="build_v2"),
        _request(),
    )
    assert promoted["explicit_promotion"] is True
    assert promoted["working_project_auto_switched_viewers"] is False
    assert promoted["source"]["project_version"] == 2
    assert promoted["source"]["build_id"] == "build_v2"


def test_presentation_transition_keeps_same_live_session_and_emergency_hide_preserves_project(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    attached = _attach(game)
    source_id = attached["source"]["source_adapter_id"]
    live_session_id = attached["source"]["live_session_id"]

    transitioned = live.transition_game_live_source(
        game.id,
        source_id,
        live.TransitionLiveSourceRequest(presentation_mode="launch_showcase"),
        _request(),
    )
    assert transitioned["same_live_session"] is True
    assert transitioned["new_live_session_created"] is False
    assert transitioned["live_session_id"] == live_session_id
    assert transitioned["source"]["source_adapter_id"] == source_id

    hidden = live.emergency_hide_game_live_source(
        game.id,
        source_id,
        live.EmergencyHideRequest(revoke=False),
        _request(),
    )
    assert hidden["source"]["status"] == "hidden"
    assert hidden["source"]["presentation_mode"] == "brb"
    assert hidden["project_deleted"] is False
    assert hidden["autosave_terminated"] is False
    assert hidden["playtest_build_deleted"] is False


def test_feedback_requires_explicit_promotion_and_return_records_are_idempotent(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    attached = _attach(game)
    source_id = attached["source"]["source_adapter_id"]
    session_id = attached["source"]["live_session_id"]

    raw_chat = live.CreateLiveFeedbackRequest(
        live_session_id=session_id,
        source_adapter_id=source_id,
        author_ref="viewer_1",
        category="idea",
        text="Add another jump pad.",
    )
    with pytest.raises(HTTPException) as blocked:
        live.create_game_live_feedback(game.id, raw_chat, _request())
    assert blocked.value.detail["code"] == "live_source_privacy_blocked"

    promoted = raw_chat.model_copy(update={"creator_promoted": True})
    feedback = live.create_game_live_feedback(game.id, promoted, _request())
    assert feedback["game_mutated"] is False
    assert feedback["chat_auto_promoted"] is False
    assert feedback["feedback"]["project_version"] == 1
    assert feedback["feedback"]["build_id"] == "build_v1"

    body = live.CreateLiveReturnRequest(
        live_session_id=session_id,
        source_adapter_id=source_id,
        asset_ref="shared_sky_clip:clip_42",
        asset_type="bug_reproduction",
        start_seconds=10,
        end_seconds=18,
        processing_state="ready",
        idempotency_key="clip_callback_42",
    )
    first = live.register_game_live_return(game.id, body, _request())
    second = live.register_game_live_return(game.id, body, _request())
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["return"]["return_id"] == second["return"]["return_id"]
    assert first["return"]["build_id"] == "build_v1"
    assert first["return"]["project_version"] == 1


def test_permission_revocation_hook_revokes_all_project_sources(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    first = _attach(game, source_type="clean_game_output")
    second = _attach(
        game,
        live_session_id="live_session_2",
        source_type="selected_scene_viewport",
        presentation_surface_ref="safe_surface:scene_view_1",
        presentation_mode="development",
    )

    assert first["source"]["revoked"] is False
    assert second["source"]["revoked"] is False
    assert live.revoke_project_live_sources(game.id) == 2

    state = live._load_state(game.id)
    assert all(source.revoked for source in state.sources.values())
    assert all(source.status == "revoked" for source in state.sources.values())
    assert all(source.presentation_mode == "brb" for source in state.sources.values())


def test_live_router_exposes_safe_source_version_feedback_return_and_portal_contracts():
    paths = {getattr(route, "path", "") for route in live.router.routes}
    assert "/api/game-forge/games/{game_id}/live" in paths
    assert "/api/game-forge/games/{game_id}/live/sources" in paths
    assert "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation" in paths
    assert "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version" in paths
    assert "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide" in paths
    assert "/api/game-forge/games/{game_id}/live/feedback" in paths
    assert "/api/game-forge/games/{game_id}/live/returns" in paths
    assert "/game-creation/live/{game_id}" in paths


def test_game_forge_live_routes_are_mounted_on_real_release_app():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        paths = production_entrypoint.app.openapi().get("paths", {})
        required = {
            ("get", "/api/game-forge/games/{game_id}/live"),
            ("post", "/api/game-forge/games/{game_id}/live/sources"),
            ("patch", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation"),
            ("post", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version"),
            ("post", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide"),
            ("post", "/api/game-forge/games/{game_id}/live/feedback"),
            ("post", "/api/game-forge/games/{game_id}/live/returns"),
        }
        missing = sorted((method, path) for method, path in required if method not in paths.get(path, {}))
        if missing:
            raise SystemExit(f"Game Forge Shared Sky LIVE routes are not composed into the production app: {missing}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
