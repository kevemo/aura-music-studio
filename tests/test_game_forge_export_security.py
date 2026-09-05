from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.game_forge_godot_export_api as godot_api


def _app():
    app = FastAPI()
    app.include_router(godot_api.router)

    @app.middleware("http")
    async def fake_member(request, call_next):
        request.state.member = SimpleNamespace(plan=SimpleNamespace(has=lambda _cap: True))
        return await call_next(request)

    return app


def test_godot_route_enforces_shared_media_budget_before_export(monkeypatch):
    game = SimpleNamespace(id="game_budget")
    monkeypatch.setattr(godot_api, "load_game", lambda _game_id: game)
    monkeypatch.setattr(godot_api, "_MAX_EXPORT_MEDIA_BYTES", 10)
    monkeypatch.setattr(godot_api, "_MAX_EXPORT_ASSETS", 10)
    monkeypatch.setattr(
        godot_api,
        "runtime_asset_manifest",
        lambda _game_id: [
            {"id": "a", "byte_size": 6},
            {"id": "b", "byte_size": 5},
        ],
    )

    called = False

    def should_not_export(_game):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(godot_api, "create_godot_source_export", should_not_export)

    with TestClient(_app()) as client:
        response = client.post("/api/game-forge/games/game_budget/exports/godot-source")

    assert response.status_code == 409
    assert "aggregate limit" in response.json()["detail"]
    assert called is False


def test_godot_route_rejects_invalid_declared_asset_size(monkeypatch):
    game = SimpleNamespace(id="game_budget")
    monkeypatch.setattr(godot_api, "load_game", lambda _game_id: game)
    monkeypatch.setattr(godot_api, "runtime_asset_manifest", lambda _game_id: [{"id": "a", "byte_size": True}])

    with TestClient(_app()) as client:
        response = client.post("/api/game-forge/games/game_budget/exports/godot-source")

    assert response.status_code == 409
    assert "invalid byte size" in response.json()["detail"]


def test_godot_capability_never_claims_production_release_readiness(monkeypatch):
    game = SimpleNamespace(id="game_capability")
    monkeypatch.setattr(godot_api, "load_game", lambda _game_id: game)

    with TestClient(_app()) as client:
        response = client.get("/api/game-forge/games/game_capability/exports/godot-source/capability")

    assert response.status_code == 200
    body = response.json()
    assert body["source_project_ready"] is True
    assert body["production_ready"] is False
    assert body["production_release_ready"] is False
    assert body["creator_generated_executable_code"] is False
    assert body["release_blockers"] == [
        "pinned_godot_headless_validation_not_verified",
        "production_release_signing_not_verified",
    ]
    assert body["resource_limits"]["max_assets"] >= 1
    assert body["resource_limits"]["max_media_bytes"] >= 1
