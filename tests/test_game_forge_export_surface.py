from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from aura_music_studio.game_forge_export import export_capabilities
from aura_music_studio.game_forge_export_portal import game_export_portal
from aura_music_studio.game_forge_world_api import router as game_world_router


def test_live_game_forge_router_mounts_export_api_and_studio():
    paths = {route.path for route in game_world_router.routes}
    assert "/api/game-forge/games/{game_id}/exports/capabilities" in paths
    assert "/api/game-forge/games/{game_id}/exports" in paths
    assert "/api/game-forge/games/{game_id}/exports/{export_id}/download" in paths
    assert "/game-creation/export/{game_id}" in paths


def test_export_studio_redirects_anonymous_member_to_signin():
    request = Request({"type": "http", "method": "GET", "path": "/game-creation/export/game_demo", "headers": []})
    response = game_export_portal("game_demo", request)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin?next=/game-creation/export/")


def test_export_studio_html_is_truthful_about_adapter_status():
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.member = SimpleNamespace(plan=SimpleNamespace(has=lambda _cap: True))
    response = game_export_portal("game_demo", request)
    body = response.body.decode("utf-8")
    assert "Game Export Studio" in body
    assert "No fake engine exports" in body
    assert "production ready" in body.lower()
    assert "planned external-engine adapters" in body


def test_capability_contract_keeps_external_engines_planned():
    caps = export_capabilities()
    assert caps["targets"]["aura_web"]["production_ready"] is True
    assert caps["targets"]["aura_web"]["executable_export"] is True
    for target in ("phaser4", "playcanvas", "babylon", "godot"):
        assert caps["targets"][target]["production_ready"] is False
        assert caps["targets"][target]["executable_export"] is False


def test_export_routes_require_membership_when_mounted_directly():
    app = FastAPI()
    app.include_router(game_world_router)
    client = TestClient(app)
    response = client.get("/api/game-forge/games/game_hidden/exports/capabilities")
    assert response.status_code == 401
