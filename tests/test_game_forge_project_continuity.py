from types import SimpleNamespace
import subprocess
import sys
import textwrap

import pytest
from fastapi import HTTPException

import aura_music_studio.game_forge_project_binding as binding
from aura_music_studio.creative_project_continuity import PROJECT_CONTINUITY_SCRIPT
from aura_music_studio.game_forge_models import GameDNA


def _game() -> GameDNA:
    return GameDNA(
        id="game_continuity",
        title="One Project Game",
        prompt="Build a game from this project's Creative DNA.",
        rights_confirmed=True,
        rights_attestation="Creator confirmed rights.",
    )


def _request():
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace()))


def test_bind_game_persists_creative_project_identity_without_rebinding(monkeypatch):
    game = _game()
    saved = []
    invalidated = []
    monkeypatch.setattr(binding, "_creative_project", lambda name: (SimpleNamespace(name=name), object()))
    monkeypatch.setattr(binding, "list_game_assets", lambda _game_id: [])
    monkeypatch.setattr(binding, "save_game", lambda row: saved.append(row.model_copy(deep=True)))
    monkeypatch.setattr(binding, "_invalidate_after_edit", lambda row: invalidated.append(row.id))

    bound, changed = binding._bind_game(game, "album-visual-world", invalidate_existing=True)

    assert changed is True
    assert binding.creative_project_name(bound) == "album-visual-world"
    assert bound.metadata["creative_project_bound"] is True
    assert bound.metadata["creative_project_continuity"] == "shared_tenant_project"
    assert invalidated == [game.id]
    assert saved[-1].metadata["creative_project_name"] == "album-visual-world"

    same, changed_again = binding._bind_game(bound, "album-visual-world", invalidate_existing=True)
    assert same is bound
    assert changed_again is False
    assert invalidated == [game.id]

    with pytest.raises(HTTPException) as exc:
        binding._bind_game(bound, "different-project", invalidate_existing=True)
    assert exc.value.status_code == 409
    assert "already bound" in str(exc.value.detail)


def test_legacy_game_with_foreign_snapshots_cannot_adopt_wrong_project(monkeypatch):
    game = _game()
    monkeypatch.setattr(binding, "_creative_project", lambda name: (SimpleNamespace(name=name), object()))
    monkeypatch.setattr(
        binding,
        "list_game_assets",
        lambda _game_id: [SimpleNamespace(source_project="older-project")],
    )

    with pytest.raises(HTTPException) as exc:
        binding._bind_game(game, "new-project", invalidate_existing=True)

    assert exc.value.status_code == 409
    assert "snapshots from another Creative project" in str(exc.value.detail)
    assert binding.creative_project_name(game) is None


def test_bound_game_library_is_scoped_to_one_creative_project(monkeypatch):
    game = _game()
    game.metadata["creative_project_name"] = "shared-project"
    project = SimpleNamespace(name="shared-project")
    captured = {}
    monkeypatch.setattr(binding, "_creative_project", lambda _name: (project, object()))

    def fake_scan(member, project_dirs=None):
        captured["member"] = member
        captured["project_dirs"] = project_dirs
        return [
            {"id": "shared-project:el_image", "project": "shared-project", "kind": "image"},
            {"id": "shared-project:el_text", "project": "shared-project", "kind": "text"},
        ]

    monkeypatch.setattr(binding, "scan_creative_library", fake_scan)
    rows, project_name = binding._project_library(object(), game)

    assert project_name == "shared-project"
    assert captured["project_dirs"] == [project]
    assert [row["id"] for row in rows] == ["shared-project:el_image"]


def test_unbound_legacy_game_keeps_global_library_compatibility(monkeypatch):
    game = _game()
    captured = {}

    def fake_scan(member, project_dirs=None):
        captured["project_dirs"] = project_dirs
        return [
            {"id": "project-a:el_a", "project": "project-a", "kind": "image"},
            {"id": "project-b:el_b", "project": "project-b", "kind": "video"},
        ]

    monkeypatch.setattr(binding, "scan_creative_library", fake_scan)
    rows, project_name = binding._project_library(object(), game)

    assert project_name is None
    assert captured["project_dirs"] is None
    assert {row["project"] for row in rows} == {"project-a", "project-b"}


def test_cross_project_import_is_rejected_before_snapshot_copy(monkeypatch):
    game = _game()
    game.metadata["creative_project_name"] = "project-a"
    attached = []
    monkeypatch.setattr(binding, "_creator", lambda _request: object())
    monkeypatch.setattr(binding, "_game", lambda _game_id: game)
    monkeypatch.setattr(binding, "_require_editable", lambda _game: None)
    monkeypatch.setattr(binding, "_creative_project", lambda name: (SimpleNamespace(name=name), object()))
    monkeypatch.setattr(binding, "attach_creative_asset", lambda game, body: attached.append((game, body)))

    body = binding.AttachGameAssetRequest(
        source_id="project-b:el_foreign",
        role="world background",
        rights_confirmed=True,
        rights_attestation="Creator confirmed rights.",
    )
    with pytest.raises(HTTPException) as exc:
        binding.import_project_game_asset(game.id, body, _request())

    assert exc.value.status_code == 409
    assert "Cross-project asset imports are blocked" in str(exc.value.detail)
    assert attached == []


def test_project_bound_create_validates_project_before_game_creation(monkeypatch):
    calls = []
    game = _game()
    monkeypatch.setattr(binding, "_creator", lambda _request: object())
    monkeypatch.setattr(binding, "_creative_project", lambda name: calls.append(("validate", name)) or (object(), object()))
    monkeypatch.setattr(binding, "create_game_for_member", lambda member, body: calls.append(("create", body.title)) or game)
    monkeypatch.setattr(binding, "save_game", lambda row: calls.append(("save", binding.creative_project_name(row))))
    monkeypatch.setattr(binding, "_public_game", lambda row: {"id": row.id, "title": row.title})

    body = binding.CreateGameRequest(
        title="Bound Game",
        prompt="Create one game inside the shared project.",
        rights_confirmed=True,
        rights_attestation="Creator confirmed rights.",
    )
    payload = binding.create_game_in_creative_project("shared-project", body, _request())

    assert calls[0] == ("validate", "shared-project")
    assert calls[1] == ("create", "Bound Game")
    assert calls[2] == ("save", "shared-project")
    assert payload["creative_project_name"] == "shared-project"
    assert payload["project_bound"] is True


def test_project_binding_router_declares_project_bound_game_endpoints():
    paths = {getattr(route, "path", "") for route in binding.router.routes}
    assert "/api/game-forge/projects/{project_name}/games" in paths
    assert "/api/game-forge/games/{game_id}/project-context" in paths
    assert "/api/game-forge/games/{game_id}/project-library" in paths
    assert "/api/game-forge/games/{game_id}/project-assets" in paths


def test_game_page_transport_uses_project_bound_endpoints_and_binding_resolution():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "installGameProjectTransport" in script
    assert "/api/game-forge/projects/${encodeURIComponent(projectName)}/games" in script
    assert "/project-library`" in script
    assert "/project-assets`" in script
    assert "resolveGameProject" in script
    assert "creative_project_name:desired" in script
    assert "commitProject(context.creative_project_name)" in script
    assert "Game Forge now persists this exact Creative DNA identity into Game DNA" in script


def test_project_bound_game_routes_are_mounted_on_real_release_app():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        schema = production_entrypoint.app.openapi()
        paths = schema.get("paths", {})
        required = {
            ("post", "/api/game-forge/projects/{project_name}/games"),
            ("get", "/api/game-forge/games/{game_id}/project-context"),
            ("post", "/api/game-forge/games/{game_id}/project-context"),
            ("get", "/api/game-forge/games/{game_id}/project-library"),
            ("post", "/api/game-forge/games/{game_id}/project-assets"),
        }
        missing = sorted((method, path) for method, path in required if method not in paths.get(path, {}))
        if missing:
            raise SystemExit(f"Game Forge project-continuity routes are not composed into the production app: {missing}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
