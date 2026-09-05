from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from aura_music_studio.game_forge_export import export_capabilities
from aura_music_studio.game_forge_export_portal import game_export_portal
from aura_music_studio.game_forge_world_api import router as game_world_router


def _mounted_app() -> FastAPI:
    app = FastAPI()
    app.include_router(game_world_router)
    return app


def test_live_game_forge_router_mounts_export_api_studio_and_godot_preview():
    # FastAPI 0.141 materializes nested included routers when the application starts. Validate the
    # started application rather than inspecting the pre-startup APIRouter placeholder list.
    with TestClient(_mounted_app()) as client:
        schema = client.get("/openapi.json").json()
        paths = set(schema.get("paths") or {})
        assert "/api/game-forge/games/{game_id}/exports/capabilities" in paths
        assert "/api/game-forge/games/{game_id}/exports" in paths
        assert "/api/game-forge/games/{game_id}/exports/godot-source/capability" in paths
        assert "/api/game-forge/games/{game_id}/exports/godot-source" in paths
        assert "/api/game-forge/games/{game_id}/exports/{export_id}/download" not in paths  # intentionally hidden download route
        response = client.get("/game-creation/export/game_demo", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/signin?next=/game-creation/export/")
        godot = client.get("/game-creation/godot-export/game_demo", follow_redirects=False)
        assert godot.status_code == 303
        assert godot.headers["location"].startswith("/signin?next=/game-creation/godot-export/")


def test_export_studio_redirects_anonymous_member_to_signin():
    request = Request({"type": "http", "method": "GET", "path": "/game-creation/export/game_demo", "headers": []})
    response = game_export_portal("game_demo", request)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin?next=/game-creation/export/")


def test_export_studio_html_is_truthful_about_package_and_release_status():
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.member = SimpleNamespace(plan=SimpleNamespace(has=lambda _cap: True))
    response = game_export_portal("game_demo", request)
    body = response.body.decode("utf-8")
    lowered = body.lower()
    assert "Game Export Studio" in body
    assert "No fake engine exports or release claims" in body
    assert "package-ready does not mean production-release-ready" in lowered
    assert "publisher authenticity" in lowered
    assert "Godot 4 Source Project" in body
    assert "developer-preview" in lowered
    assert "full aura runtime parity" in lowered


def test_capability_contract_separates_aura_web_package_from_production_release():
    # Aura Web can generate a verified executable package, but release readiness stays false until
    # independently trusted publisher signing is verified. External engine adapters remain planned.
    caps = export_capabilities()
    aura_web = caps["targets"]["aura_web"]
    assert aura_web["package_ready"] is True
    assert aura_web["production_ready"] is False
    assert aura_web["production_release_ready"] is False
    assert aura_web["executable_export"] is True
    assert aura_web["publisher_authenticity_verified"] is False
    assert "publisher_authenticity_not_verified" in aura_web["release_blockers"]
    for target in ("phaser4", "playcanvas", "babylon", "godot"):
        assert caps["targets"][target]["package_ready"] is False
        assert caps["targets"][target]["production_ready"] is False
        assert caps["targets"][target]["production_release_ready"] is False
        assert caps["targets"][target]["executable_export"] is False


def test_export_routes_require_membership_when_mounted_directly():
    with TestClient(_mounted_app()) as client:
        response = client.get("/api/game-forge/games/game_hidden/exports/capabilities")
        assert response.status_code == 401
        godot = client.get("/api/game-forge/games/game_hidden/exports/godot-source/capability")
        assert godot.status_code == 401
