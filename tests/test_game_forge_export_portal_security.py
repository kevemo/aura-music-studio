from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.game_forge_export_portal as portal


def _app():
    app = FastAPI()
    app.include_router(portal.router)

    @app.middleware("http")
    async def fake_member(request, call_next):
        request.state.member = SimpleNamespace(plan=SimpleNamespace(has=lambda _cap: True))
        return await call_next(request)

    return app


def test_export_portal_does_not_claim_aura_web_is_production_release_ready(monkeypatch):
    monkeypatch.setattr(portal, "_project_context_for_export", lambda _game_id: "")

    with TestClient(_app()) as client:
        response = client.get("/game-creation/export/game_truth")

    assert response.status_code == 200
    text = response.text
    assert "Production-ready export" not in text
    assert "Verified package export" in text
    assert "Package-ready does not mean production-release-ready" in text
    assert "Production release remains separately gated by trusted signing evidence" in text
    assert "row.production_release_ready" in text
    assert "row.package_ready" in text


def test_export_portal_target_badge_uses_release_readiness_not_package_readiness(monkeypatch):
    monkeypatch.setattr(portal, "_project_context_for_export", lambda _game_id: "")

    with TestClient(_app()) as client:
        response = client.get("/game-creation/export/game_truth")

    text = response.text
    assert "Package ready · release gated" in text
    assert "Production release ready" in text
    # The badge is green only from the explicit release-ready flag.
    assert "releaseReady?'ready':'planned'" in text
