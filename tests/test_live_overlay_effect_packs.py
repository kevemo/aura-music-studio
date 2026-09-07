from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import aura_music_studio.aura_live_overlay_effect_packs as packs
import aura_music_studio.aura_live_overlay_effects as effects
import aura_music_studio.aura_live_overlay_studio as studio
from aura_music_studio.aura_live_overlay_interactives import router as interactive_router


def _client(tmp_path, monkeypatch):
    db = tmp_path / "live-effect-packs.sqlite3"
    monkeypatch.setattr(studio, "DB_PATH", db)
    monkeypatch.setattr(effects, "DB_PATH", db)
    studio._init_schema()
    effects._init_schema()
    packs._init_schema()

    app = FastAPI()

    @app.middleware("http")
    async def fake_member(request: Request, call_next):
        user_id = request.headers.get("x-test-user", "streamer-1")
        request.state.member = SimpleNamespace(
            user_id=user_id,
            plan=SimpleNamespace(id="free", name="Free"),
        )
        return await call_next(request)

    app.include_router(interactive_router)
    return TestClient(app)


def test_builtin_packs_are_valid_and_cover_distinct_live_styles():
    assert len(packs.BUILTIN_PACKS) >= 8
    ids = [pack["id"] for pack in packs.BUILTIN_PACKS]
    assert len(ids) == len(set(ids))
    assert {
        "clean-minimal",
        "gaming-energy",
        "music-stage",
        "just-chatting",
        "battle-mode",
        "creator-growth",
        "cosmic-live",
        "calm-accessible",
    } <= set(ids)
    for pack in packs.BUILTIN_PACKS:
        assert pack["enabled_effects"]
        assert set(pack["enabled_effects"]) <= set(effects.EFFECT_BY_ID)
        assert 0.25 <= pack["intensity"] <= 2.0
        assert 1 <= pack["max_effects_per_event"] <= effects.MAX_EFFECTS_PER_EVENT


def test_effect_pack_routes_are_mounted_on_existing_live_overlay_router():
    app = FastAPI()
    app.include_router(interactive_router)
    paths = set(app.openapi()["paths"])
    assert "/api/live-overlays/effect-packs" in paths
    assert "/api/live-overlays/effect-packs/{pack_id}/apply" in paths
    assert "/api/live-overlays/effect-packs/custom" in paths
    assert "/api/live-overlays/effect-packs/custom/{pack_id}" in paths


def test_free_streamer_can_apply_builtin_pack_and_runtime_preferences_change(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/live-overlays/effect-packs/gaming-energy/apply")
    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"] is True
    assert payload["pack"]["id"] == "gaming-energy"
    assert payload["current"]["enabled_effects"] == packs.BUILTIN_BY_ID["gaming-energy"]["enabled_effects"]
    assert payload["current"]["intensity"] == packs.BUILTIN_BY_ID["gaming-energy"]["intensity"]

    runtime = client.get("/api/live-overlays/effects").json()
    assert runtime["enabled_effects"] == packs.BUILTIN_BY_ID["gaming-energy"]["enabled_effects"]
    assert runtime["max_effects_per_event"] == packs.BUILTIN_BY_ID["gaming-energy"]["max_effects_per_event"]


def test_calm_pack_turns_on_reduced_motion(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/live-overlays/effect-packs/calm-accessible/apply")
    assert response.status_code == 200
    assert response.json()["current"]["reduced_motion"] is True
    assert client.get("/api/live-overlays/effects").json()["reduced_motion"] is True


def test_custom_pack_saves_current_live_setup_and_can_be_reapplied(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    chosen = ["gift_cannon", "star_drift", "subscriber_fireworks"]
    saved_live = client.put(
        "/api/live-overlays/effects",
        json={
            "enabled_effects": chosen,
            "intensity": 1.35,
            "max_effects_per_event": 7,
            "reduced_motion": False,
        },
    )
    assert saved_live.status_code == 200

    created = client.post(
        "/api/live-overlays/effect-packs/custom",
        json={"name": "Kev LIVE", "description": "Reusable test pack"},
    )
    assert created.status_code == 200
    pack = created.json()["pack"]
    assert pack["enabled_effects"] == chosen
    assert pack["intensity"] == 1.35
    assert pack["max_effects_per_event"] == 7

    assert client.post("/api/live-overlays/effect-packs/clean-minimal/apply").status_code == 200
    reapplied = client.post(f"/api/live-overlays/effect-packs/{pack['id']}/apply")
    assert reapplied.status_code == 200
    assert reapplied.json()["current"]["enabled_effects"] == chosen
    assert reapplied.json()["current"]["intensity"] == 1.35


def test_custom_packs_are_tenant_scoped(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    created = client.post(
        "/api/live-overlays/effect-packs/custom",
        json={"name": "Private Stream Pack", "description": "streamer one only"},
        headers={"x-test-user": "streamer-1"},
    )
    assert created.status_code == 200
    pack_id = created.json()["pack"]["id"]

    other = client.get("/api/live-overlays/effect-packs", headers={"x-test-user": "streamer-2"})
    assert other.status_code == 200
    assert all(pack["id"] != pack_id for pack in other.json()["custom"])
    denied = client.post(
        f"/api/live-overlays/effect-packs/{pack_id}/apply",
        headers={"x-test-user": "streamer-2"},
    )
    assert denied.status_code == 404


def test_unknown_effect_in_custom_pack_fails_closed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/live-overlays/effect-packs/custom",
        json={"name": "Bad Pack", "enabled_effects": ["not-a-real-effect"]},
    )
    assert response.status_code == 400
    assert "Unknown LIVE overlay effect" in response.json()["detail"]


def test_custom_pack_can_be_deleted_without_touching_live_preferences(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/live-overlays/effect-packs/music-stage/apply")
    before = client.get("/api/live-overlays/effects").json()["enabled_effects"]
    created = client.post(
        "/api/live-overlays/effect-packs/custom",
        json={"name": "Temporary Pack"},
    ).json()["pack"]
    deleted = client.delete(f"/api/live-overlays/effect-packs/custom/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    after = client.get("/api/live-overlays/effects").json()["enabled_effects"]
    assert after == before


def test_effect_pack_page_is_private_no_store_and_has_one_click_controls(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/live-overlay-studio/effect-packs")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "One-click LIVE Effect Packs" in response.text
    assert "Gaming Energy" in response.text
    assert "Calm &amp; Accessible" in response.text
    assert "Use this LIVE pack" in response.text
