from types import SimpleNamespace
import subprocess
import sys
import textwrap

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

import aura_music_studio.game_forge_project_navigation as navigation
import aura_music_studio.game_forge_project_navigation_middleware as navigation_middleware
from aura_music_studio.creative_project_continuity import PROJECT_CONTINUITY_SCRIPT
from aura_music_studio.game_forge_models import GameDNA


GAME_ID = "game_project_nav"
PROJECT = "shared-creative-project"


def _game(project_name: str | None = PROJECT) -> GameDNA:
    metadata = {"creative_project_name": project_name} if project_name else {}
    return GameDNA(
        id=GAME_ID,
        title="Project Navigation Game",
        prompt="Keep all Game Forge tools in one Creative project context.",
        rights_confirmed=True,
        rights_attestation="Creator confirmed rights.",
        metadata=metadata,
    )


def test_subworkspace_allowlist_is_explicit_and_rejects_api_gallery_download_and_bad_ids():
    workspaces = [
        "play",
        "export",
        "capture-card",
        "godot-export",
        "state-machines",
        "world-events",
        "adventure",
        "gameplay",
        "world-logic",
    ]
    for workspace in workspaces:
        assert navigation.game_id_from_subworkspace_path(f"/game-creation/{workspace}/{GAME_ID}") == GAME_ID

    assert navigation.game_id_from_subworkspace_path(f"/api/game-forge/games/{GAME_ID}") is None
    assert navigation.game_id_from_subworkspace_path(f"/game-gallery/{GAME_ID}") is None
    assert navigation.game_id_from_subworkspace_path(f"/api/game-forge/games/{GAME_ID}/exports/current/download") is None
    assert navigation.game_id_from_subworkspace_path("/game-creation/adventure/not-a-game-id") is None
    assert navigation.game_id_from_subworkspace_path(f"/game-creation/adventure/{GAME_ID}/extra") is None


def test_project_identity_is_read_directly_from_game_dna_without_creator_api(monkeypatch):
    game = _game()
    monkeypatch.setattr(navigation, "load_game", lambda _game_id: game)

    assert navigation.bound_project_name_for_game(GAME_ID) == PROJECT

    game.metadata = {}
    assert navigation.bound_project_name_for_game(GAME_ID) == ""


def test_navigation_script_uses_authoritative_project_and_exact_game_only():
    script = navigation.project_navigation_script(GAME_ID, PROJECT)

    assert f'const GAME_ID="{GAME_ID}",PROJECT_NAME="{PROJECT}"' in script
    assert "url.pathname==='/game-creation'" in script
    assert "url.searchParams.set('project',PROJECT_NAME)" in script
    assert "url.searchParams.set('game',GAME_ID)" in script
    assert "decodeURIComponent(match[1])!==GAME_ID" in script
    assert "url.origin!==location.origin" in script
    assert "history.replaceState" in script
    assert "MutationObserver" in script
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
    assert "WebSocket" not in script


def _middleware_app(monkeypatch, project_name: str):
    monkeypatch.setattr(navigation_middleware, "bound_project_name_for_game", lambda _game_id: project_name)
    app = FastAPI()

    @app.get(f"/game-creation/adventure/{GAME_ID}")
    def page():
        return HTMLResponse(
            "<html><body>"
            "<a href='/game-creation?project=forged'>Game Workspace</a>"
            f"<a href='/game-creation/world-logic/{GAME_ID}?project=forged'>World Logic</a>"
            "<a href='https://example.com/game-creation'>External</a>"
            "</body></html>"
        )

    @app.get(f"/api/game-forge/games/{GAME_ID}")
    def api():
        return JSONResponse({"id": GAME_ID})

    app.add_middleware(navigation_middleware.GameForgeProjectNavigationMiddleware)
    return app


def test_middleware_injects_server_authoritative_navigation_after_route_access(monkeypatch):
    app = _middleware_app(monkeypatch, PROJECT)
    client = TestClient(app)

    response = client.get(f"/game-creation/adventure/{GAME_ID}?project=forged")

    assert response.status_code == 200
    assert response.text.count("data-game-project-continuity='1'") == 1
    assert f'PROJECT_NAME="{PROJECT}"' in response.text
    assert "project=forged" in response.text  # source link remains until the injected script normalizes it client-side
    assert "GameForgeProjectNavigation" in response.text


def test_unbound_legacy_game_and_non_html_api_are_left_unchanged(monkeypatch):
    app = _middleware_app(monkeypatch, "")
    client = TestClient(app)

    page = client.get(f"/game-creation/adventure/{GAME_ID}?project=legacy-query")
    api = client.get(f"/api/game-forge/games/{GAME_ID}")

    assert "data-game-project-continuity" not in page.text
    assert page.status_code == 200
    assert api.json() == {"id": GAME_ID}
    assert "data-game-project-continuity" not in api.text


def test_production_app_mounts_navigation_middleware_and_keeps_canonical_project_query_contract():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        middleware = [entry.cls.__name__ for entry in production_entrypoint.app.user_middleware]
        if "GameForgeProjectNavigationMiddleware" not in middleware:
            raise SystemExit("Game Forge project navigation middleware is not mounted in production")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "const requested=(new URLSearchParams(location.search).get('project')||'').trim();" in PROJECT_CONTINUITY_SCRIPT
