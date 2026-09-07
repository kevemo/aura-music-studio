from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.video_sync_entitlement_routes as gates
from aura_music_studio.creative_version_autopromotion import router as production_router
from aura_music_studio.plans import get_plan


def _app(plan_id: str) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def member_context(request, call_next):
        request.state.member = SimpleNamespace(user_id="member-1", plan=get_plan(plan_id))
        return await call_next(request)

    app.include_router(production_router)
    return app


_CASES = [
    ("get", "/creative/projects/song/video-music-sync", None, "base_get_video_music_sync"),
    ("put", "/creative/projects/song/video-music-sync", {"markers": []}, "base_replace_video_music_sync"),
    ("post", "/creative/projects/song/video-music-sync/snap-plan", {}, "base_plan_scene_boundary_snaps"),
    (
        "post",
        "/creative/projects/song/video-music-sync/apply-snaps",
        {"snaps": [{"scene_id": "scene-1", "boundary": "start", "marker_id": "beat-1"}]},
        "base_apply_scene_boundary_snaps",
    ),
    (
        "post",
        "/creative/projects/song/video-music-sync/analyze-audio",
        {"element_id": "el_audio"},
        "base_analyze_project_audio",
    ),
    (
        "post",
        "/creative/projects/song/music-video/storyboard-plan",
        {"project_end_seconds": 12},
        "base_plan_music_video_storyboard",
    ),
    (
        "post",
        "/creative/projects/song/music-video/storyboard-apply",
        {"project_end_seconds": 12, "replace_existing": False},
        "base_apply_music_video_storyboard",
    ),
]


@pytest.mark.parametrize("method,path,payload,target_name", _CASES)
def test_basic_cannot_reach_video_sync_handlers(monkeypatch, method, path, payload, target_name):
    called = {"value": False}

    def target(*_args, **_kwargs):
        called["value"] = True
        return {"ok": True}

    monkeypatch.setattr(gates, target_name, target)
    client = TestClient(_app("base"))
    response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
    assert response.status_code == 403
    assert "Pro membership" in response.json()["detail"]
    assert called["value"] is False


@pytest.mark.parametrize("method,path,payload,target_name", _CASES)
def test_pro_routes_dispatch_to_existing_handlers(monkeypatch, method, path, payload, target_name):
    called = {"value": False}

    def target(*_args, **_kwargs):
        called["value"] = True
        return {"ok": True, "source": target_name}

    monkeypatch.setattr(gates, target_name, target)
    client = TestClient(_app("pro"))
    response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "source": target_name}
    assert called["value"] is True


def test_missing_membership_is_rejected_before_handler(monkeypatch):
    called = {"value": False}

    def target(*_args, **_kwargs):
        called["value"] = True
        return {"ok": True}

    monkeypatch.setattr(gates, "base_get_video_music_sync", target)
    app = FastAPI()
    app.include_router(gates.router)
    response = TestClient(app).get("/creative/projects/song/video-music-sync")
    assert response.status_code == 401
    assert called["value"] is False


def test_gate_does_not_grant_roles_or_change_plan():
    request = SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id="member-1", plan=get_plan("pro"))))
    before = request.state.member.plan.id
    returned = gates.require_video_sync_entitlement(request)
    assert returned is request.state.member
    assert request.state.member.plan.id == before == "pro"
