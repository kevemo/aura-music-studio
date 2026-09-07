from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.access_control import PUBLIC_PREFIXES
import aura_music_studio.aura_live_overlay_effects as effects
import aura_music_studio.aura_live_overlay_studio as studio
from aura_music_studio.aura_live_overlay_interactives import router as interactive_router


SUPPORTED_PRIMITIVES = {
    "fountain",
    "rain",
    "burst",
    "confetti",
    "ring",
    "flash",
    "frame",
    "banner",
    "spotlight",
    "shake",
    "zoom",
    "bounce",
    "glitch",
    "ambient",
    "ambient_band",
}


def _client(tmp_path, monkeypatch, *, plan_id="free"):
    db = tmp_path / "live-effects.sqlite3"
    monkeypatch.setattr(studio, "DB_PATH", db)
    monkeypatch.setattr(effects, "DB_PATH", db)
    studio._init_schema()
    effects._init_schema()

    app = FastAPI()

    @app.middleware("http")
    async def fake_member(request: Request, call_next):
        request.state.member = SimpleNamespace(
            user_id="streamer-1",
            plan=SimpleNamespace(id=plan_id, name={"free": "Free", "base": "Basic", "pro": "Pro"}[plan_id]),
        )
        return await call_next(request)

    app.include_router(interactive_router)
    return TestClient(app), db


def test_catalogue_is_large_unique_and_fully_executable():
    ids = [item["id"] for item in effects.EFFECT_CATALOG]
    assert len(ids) >= 50
    assert len(ids) == len(set(ids))
    assert set(item["primitive"] for item in effects.EFFECT_CATALOG) <= SUPPORTED_PRIMITIVES
    assert all(item["events"] for item in effects.EFFECT_CATALOG)
    assert all(set(item["events"]) <= (effects.EVENT_TYPES | {"ambient"}) for item in effects.EFFECT_CATALOG)
    assert {"gift_cannon", "heart_fountain", "follower_confetti", "subscriber_fireworks", "battle_neon_frame"} <= set(ids)


def test_effect_routes_are_mounted_through_existing_interactive_router():
    app = FastAPI()
    app.include_router(interactive_router)
    paths = set(app.openapi()["paths"])
    assert "/api/live-overlays/effects" in paths
    assert "/api/live-overlays/effects/test" in paths
    assert "/api/live-overlays/effects/rotate-source" in paths


def test_free_streamer_can_select_any_effect_without_effect_tier_lock(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch, plan_id="free")
    initial = client.get("/api/live-overlays/effects")
    assert initial.status_code == 200
    data = initial.json()
    assert data["all_effects_selectable"] is True
    assert data["browser_source_runtime"] is True
    assert data["effect_count"] == len(effects.EFFECT_CATALOG)

    all_ids = [item["id"] for item in effects.EFFECT_CATALOG]
    saved = client.put(
        "/api/live-overlays/effects",
        json={
            "enabled_effects": all_ids,
            "intensity": 1.4,
            "max_effects_per_event": 8,
            "reduced_motion": False,
        },
    )
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["enabled_effects"] == all_ids
    assert payload["intensity"] == 1.4
    assert payload["max_effects_per_event"] == 8


def test_unknown_effect_id_fails_closed(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    response = client.put("/api/live-overlays/effects", json={"enabled_effects": ["not-a-real-effect"]})
    assert response.status_code == 400
    assert "Unknown LIVE overlay effect" in response.json()["detail"]


def test_effect_preferences_are_persistent_and_bounded(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    selected = ["gift_cannon", "battle_lightning", "star_drift"]
    response = client.put(
        "/api/live-overlays/effects",
        json={
            "enabled_effects": selected,
            "intensity": 2,
            "max_effects_per_event": effects.MAX_EFFECTS_PER_EVENT,
            "reduced_motion": True,
        },
    )
    assert response.status_code == 200
    again = client.get("/api/live-overlays/effects").json()
    assert again["enabled_effects"] == selected
    assert again["intensity"] == 2
    assert again["max_effects_per_event"] == effects.MAX_EFFECTS_PER_EVENT
    assert again["reduced_motion"] is True


def test_synthetic_effect_preview_queues_only_overlay_event(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    response = client.post("/api/live-overlays/effects/test", json={"event_type": "gift"})
    assert response.status_code == 200
    assert response.json()["synthetic"] is True
    assert response.json()["goal_state_changed"] is False

    with effects._connect() as con:
        row = con.execute(
            "SELECT event_type,payload_json FROM live_overlay_events WHERE user_id=? ORDER BY id DESC LIMIT 1",
            ("streamer-1",),
        ).fetchone()
    assert row["event_type"] == "gift"
    assert json.loads(row["payload_json"])["synthetic"] is True
    assert db.exists()


def test_effect_source_rotation_uses_same_private_overlay_secret_and_invalidates_old_url(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    first = client.post("/api/live-overlays/effects/rotate-source")
    assert first.status_code == 200
    first_url = first.json()["source_url"]
    assert "/live-overlay/source/effects/" in first_url
    assert first.json()["invalidates_previous_overlay_source_urls"] is True
    first_token = urlsplit(first_url).path.rsplit("/", 1)[-1]
    assert client.get(f"/live-overlay/source/effects/{first_token}/state").status_code == 200

    second = client.post("/api/live-overlays/effects/rotate-source")
    assert second.status_code == 200
    second_token = urlsplit(second.json()["source_url"]).path.rsplit("/", 1)[-1]
    assert second_token != first_token
    assert client.get(f"/live-overlay/source/effects/{first_token}/state").status_code == 404
    assert client.get(f"/live-overlay/source/effects/{second_token}/state").status_code == 200


def test_effect_browser_source_is_transparent_bounded_and_token_protected(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    rotated = client.post("/api/live-overlays/effects/rotate-source").json()
    token = urlsplit(rotated["source_url"]).path.rsplit("/", 1)[-1]

    denied = client.get("/live-overlay/source/effects/not-the-token")
    assert denied.status_code == 404

    response = client.get(f"/live-overlay/source/effects/{token}")
    assert response.status_code == 200
    body = response.text
    assert "background:transparent" in body
    assert "function run(spec,e,p)" in body
    assert "function handle(ev)" in body
    assert "max_effects_per_event" in body
    assert "eval(" not in body
    assert "new Function" not in body
    assert "<script src=" not in body
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_browser_effect_sources_are_public_only_at_middleware_layer():
    assert "/live-overlay/source/" in PUBLIC_PREFIXES
