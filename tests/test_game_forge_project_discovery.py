from types import SimpleNamespace
import subprocess
import sys
import textwrap

import pytest
from fastapi import HTTPException

import aura_music_studio.game_forge_project_binding as binding
from aura_music_studio.creative_project_continuity import PROJECT_CONTINUITY_SCRIPT
from aura_music_studio.game_forge_models import GameDNA
from aura_music_studio.plans import GAME_CREATE, GAME_CREATE_UNLIMITED


def _game(game_id: str, project_name: str | None) -> GameDNA:
    metadata = {}
    if project_name:
        metadata = {
            "creative_project_name": project_name,
            "creative_project_bound": True,
            "creative_project_continuity": "shared_tenant_project",
        }
    return GameDNA(
        id=game_id,
        title=f"Game {game_id}",
        prompt="A project-bound Game DNA test.",
        rights_confirmed=True,
        rights_attestation="Creator confirmed rights.",
        metadata=metadata,
    )


def _request(*features: str):
    enabled = set(features)
    plan = SimpleNamespace(has=lambda capability: capability in enabled)
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(plan=plan)))


def test_project_game_listing_only_returns_games_bound_to_requested_project(monkeypatch):
    project_a = _game("game_a", "project-a")
    project_b = _game("game_b", "project-b")
    legacy = _game("game_legacy", None)
    validated = []

    monkeypatch.setattr(binding, "_creative_project", lambda name: validated.append(name) or (object(), object()))
    monkeypatch.setattr(binding, "list_games", lambda: [project_a, project_b, legacy])
    monkeypatch.setattr(binding, "active_editable_games", lambda: [project_a, project_b, legacy])

    payload = binding.games_in_creative_project("project-a", _request(GAME_CREATE))

    assert validated == ["project-a"]
    assert [row["id"] for row in payload["games"]] == ["game_a"]
    assert payload["games"][0]["creative_project_name"] == "project-a"
    assert payload["games"][0]["project_bound"] is True
    assert payload["creative_project_name"] == "project-a"
    assert payload["project_bound_view"] is True
    assert payload["active_editable_count"] == 3
    assert payload["basic_active_limit"] == 1


def test_project_game_listing_preserves_unlimited_entitlement_without_scoping_global_count(monkeypatch):
    project_a = _game("game_a", "project-a")
    foreign = _game("game_b", "project-b")
    monkeypatch.setattr(binding, "_creative_project", lambda name: (object(), object()))
    monkeypatch.setattr(binding, "list_games", lambda: [project_a, foreign])
    monkeypatch.setattr(binding, "active_editable_games", lambda: [project_a, foreign])

    payload = binding.games_in_creative_project(
        "project-a",
        _request(GAME_CREATE, GAME_CREATE_UNLIMITED),
    )

    assert payload["unlimited_active_projects"] is True
    assert payload["basic_active_limit"] is None
    assert payload["active_editable_count"] == 2
    assert [row["id"] for row in payload["games"]] == ["game_a"]


def test_project_game_listing_validates_creative_project_before_store_enumeration(monkeypatch):
    enumerated = []

    def reject(_name):
        raise HTTPException(404, "Creative project not found")

    monkeypatch.setattr(binding, "_creative_project", reject)
    monkeypatch.setattr(binding, "list_games", lambda: enumerated.append(True) or [])

    with pytest.raises(HTTPException) as exc:
        binding.games_in_creative_project("missing-project", _request(GAME_CREATE))

    assert exc.value.status_code == 404
    assert enumerated == []


def test_game_project_transport_scopes_native_list_and_create_calls_to_same_project_endpoint():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "url.pathname==='/api/game-forge/games'&&method==='GET'" in script
    assert "url.pathname==='/api/game-forge/games'&&method==='POST'" in script
    assert "url.pathname=`/api/game-forge/projects/${encodeURIComponent(projectName)}/games`" in script
    assert "refreshGameProjectList" in script
    assert "try{await refreshGameProjectList()}" in script
    assert "unrelated Game DNA never remains visible" in script


def test_project_game_list_and_create_routes_are_mounted_on_real_release_app():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        schema = production_entrypoint.app.openapi()
        methods = schema.get("paths", {}).get("/api/game-forge/projects/{project_name}/games", {})
        required = {"get", "post"}
        missing = sorted(required.difference(methods))
        if missing:
            raise SystemExit(f"Project-bound Game Forge discovery/create routes are not composed into production: {missing}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
