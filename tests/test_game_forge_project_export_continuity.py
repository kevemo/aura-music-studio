from types import SimpleNamespace

import aura_music_studio.game_forge_export_portal as export_portal
import aura_music_studio.game_forge_export_readiness as readiness
import aura_music_studio.game_forge_project_binding as binding
from aura_music_studio.creative_project_continuity import PROJECT_CONTINUITY_SCRIPT
from aura_music_studio.game_forge_models import GameDNA


def _game() -> GameDNA:
    return GameDNA(
        id="game_project_export",
        title="Project Export Game",
        prompt="Build an integrity-bound project export.",
        rights_confirmed=True,
        rights_attestation="Creator confirmed rights.",
    )


def test_export_readiness_delegates_to_real_export_admission_without_creating_package(monkeypatch):
    game = _game()
    calls = []
    monkeypatch.setattr(
        readiness,
        "_validate_exportable",
        lambda row, target: calls.append((row.id, target)) or "a" * 64,
    )

    payload = readiness.aura_web_export_readiness(game)

    assert calls == [(game.id, "aura_web")]
    assert payload["ready"] is True
    assert payload["production_ready_target"] is True
    assert payload["content_hash"] == "a" * 64
    assert payload["export_studio_url"] == f"/game-creation/export/{game.id}"
    assert payload["side_effect_free_check"] is True
    assert payload["private_paths_exposed"] is False


def test_export_readiness_surfaces_authoritative_blocker_without_side_effects(monkeypatch):
    game = _game()

    def blocked(_game, _target):
        raise ValueError("Game build is missing or stale. Rebuild the current game before export")

    monkeypatch.setattr(readiness, "_validate_exportable", blocked)
    payload = readiness.aura_web_export_readiness(game)

    assert payload["ready"] is False
    assert "missing or stale" in payload["reason"]
    assert payload["content_hash"] is None
    assert payload["production_ready_target"] is True


def test_project_game_payload_carries_export_readiness_from_single_authority(monkeypatch):
    game = _game()
    game.metadata["creative_project_name"] = "album-world"
    monkeypatch.setattr(binding, "_public_game", lambda row: {"id": row.id, "title": row.title})
    monkeypatch.setattr(
        binding,
        "aura_web_export_readiness",
        lambda row: {"target": "aura_web", "ready": True, "content_hash": "hash"},
    )

    payload = binding._project_game_payload(game)

    assert payload["id"] == game.id
    assert payload["creative_project_name"] == "album-world"
    assert payload["project_bound"] is True
    assert payload["aura_web_export"] == {"target": "aura_web", "ready": True, "content_hash": "hash"}


def test_shared_workspace_only_surfaces_export_action_when_authoritative_readiness_passes():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "creativeProjectExportGame" in script
    assert "const exportState=latest.aura_web_export||{}" in script
    assert "exportState.ready?'export ready':'export blocked'" in script
    assert "payload.can_create&&latest.id&&exportState.ready&&exportState.production_ready_target" in script
    assert "exportGame.href=projectGameExportHref(latest.id,clean)" in script
    assert "if(exportGame)exportGame.hidden=true" in script


def test_project_export_deep_link_preserves_project_and_canonical_query_read():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "function projectGameExportHref(gameId,projectName=currentProject())" in script
    assert "query.set('project',cleanProject)" in script
    assert "`/game-creation/export/${encodeURIComponent(cleanGame)}`" in script
    # Preserve the compatibility repair merged by the concurrent full-site integration work.
    assert "const requested=(new URLSearchParams(location.search).get('project')||'').trim();" in script


def test_export_portal_uses_game_dna_binding_as_authoritative_project_context(monkeypatch):
    game = _game()
    game.metadata["creative_project_name"] = "bound-project"
    monkeypatch.setattr(export_portal, "load_game", lambda _game_id: game)

    assert export_portal._project_context_for_export(game.id) == "bound-project"


def test_export_portal_returns_to_exact_project_game_workspace(monkeypatch):
    monkeypatch.setattr(export_portal, "_project_context_for_export", lambda _game_id: "bound-project")
    member = SimpleNamespace(plan=SimpleNamespace(has=lambda _capability: True))
    request = SimpleNamespace(state=SimpleNamespace(member=member))

    response = export_portal.game_export_portal("game_project_export", request)
    html = response.body.decode("utf-8")

    assert 'PROJECT_NAME="bound-project"' in html
    assert "function gameHome()" in html
    assert "url.searchParams.set('project',PROJECT_NAME)" in html
    assert "url.searchParams.set('game',GAME_ID)" in html
    assert "$('gameHomeLink').href=gameHome()" in html
    assert "One Project Workspace · ${PROJECT_NAME}" in html
