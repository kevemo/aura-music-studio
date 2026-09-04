from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import aura_music_studio.aura_live_overlay_effects as effects
import aura_music_studio.aura_live_overlay_interactives as interactives
import aura_music_studio.aura_live_overlay_pro_source as pro_source
import aura_music_studio.aura_live_overlay_studio as studio
from aura_music_studio.aura_live_overlay_interactives import router as interactive_router


def _client(tmp_path, monkeypatch, *, plan_id="free"):
    db = tmp_path / "live-unified.sqlite3"
    monkeypatch.setattr(studio, "DB_PATH", db)
    monkeypatch.setattr(effects, "DB_PATH", db)
    monkeypatch.setattr(pro_source, "DB_PATH", db)
    monkeypatch.setattr(interactives, "DB_PATH", db)
    studio._init_schema()
    effects._init_schema()
    interactives._init_schema()

    app = FastAPI()

    @app.middleware("http")
    async def fake_member(request: Request, call_next):
        request.state.member = SimpleNamespace(
            user_id="streamer-unified",
            plan=SimpleNamespace(id=plan_id, name={"free": "Free", "base": "Basic", "pro": "Pro"}[plan_id]),
        )
        return await call_next(request)

    app.include_router(interactive_router)
    return TestClient(app), db


def test_unified_routes_are_mounted_through_existing_live_router():
    app = FastAPI()
    app.include_router(interactive_router)
    paths = set(app.openapi()["paths"])
    assert "/api/live-overlays/unified-source" in paths
    assert "/api/live-overlays/unified-source/rotate" in paths


def test_unified_status_reuses_existing_authorities(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    response = client.get("/api/live-overlays/unified-source")
    assert response.status_code == 200
    data = response.json()
    assert data["single_source"] is True
    assert data["uses_existing_overlay_token"] is True
    assert data["uses_existing_event_stream"] is True
    assert data["tts_uses_existing_speech_endpoint"] is True
    assert data["effect_count"] >= 50


def test_unified_rotation_invalidates_every_old_overlay_url(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    first = client.post("/api/live-overlays/unified-source/rotate")
    assert first.status_code == 200
    first_data = first.json()
    first_token = urlsplit(first_data["source_url"]).path.rsplit("/", 1)[-1]
    assert first_data["shared_overlay_credential"] is True
    assert first_data["invalidates_previous_aura_live_source_urls"] is True
    assert client.get(f"/live-overlay/source/unified/{first_token}/state").status_code == 200
    assert client.get(f"/live-overlay/source/effects/{first_token}/state").status_code == 200

    second = client.post("/api/live-overlays/unified-source/rotate")
    assert second.status_code == 200
    second_token = urlsplit(second.json()["source_url"]).path.rsplit("/", 1)[-1]
    assert second_token != first_token
    assert client.get(f"/live-overlay/source/unified/{first_token}/state").status_code == 404
    assert client.get(f"/live-overlay/source/effects/{first_token}/state").status_code == 404
    assert client.get(f"/live-overlay/source/unified/{second_token}/state").status_code == 200


def test_unified_state_combines_advanced_scene_profile_and_live_effect_runtime(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch, plan_id="base")
    rotated = client.post("/api/live-overlays/unified-source/rotate").json()
    token = urlsplit(rotated["source_url"]).path.rsplit("/", 1)[-1]

    chosen = ["gift_cannon", "heart_fountain", "cosmic_dust"]
    update = client.put(
        "/api/live-overlays/effects",
        json={"enabled_effects": chosen, "intensity": 1.35, "max_effects_per_event": 5, "reduced_motion": False},
    )
    assert update.status_code == 200

    state = client.get(f"/live-overlay/source/unified/{token}/state")
    assert state.status_code == 200
    data = state.json()
    assert data["single_source"] is True
    assert data["event_authority"] == "normalized_live_overlay_events"
    assert data["provider_authority_widened"] is False
    assert "scene" in data["advanced"]
    assert data["advanced"]["profile"]["tts_gifts_enabled"] is True
    assert "source_token_hash" not in data["advanced"]["profile"]
    assert data["effects"]["enabled_effects"] == chosen
    assert data["effects"]["intensity"] == 1.35
    assert data["effects"]["max_effects_per_event"] == 5


def test_unified_event_endpoint_reads_canonical_normalized_queue(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    rotated = client.post("/api/live-overlays/unified-source/rotate").json()
    token = urlsplit(rotated["source_url"]).path.rsplit("/", 1)[-1]

    with studio._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_events(user_id,event_type,payload_json,created_at) VALUES(?,?,?,datetime('now'))",
            ("streamer-unified", "gift", json.dumps({"username": "Viewer", "gift_name": "Rose", "coins": 1})),
        )

    events = client.get(f"/live-overlay/source/unified/{token}/events?after=0")
    assert events.status_code == 200
    rows = events.json()["events"]
    assert len(rows) == 1
    assert rows[0]["event_type"] == "gift"
    assert rows[0]["payload"]["gift_name"] == "Rose"


def test_unified_browser_source_executes_widgets_effects_and_existing_tts(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch, plan_id="base")
    rotated = client.post("/api/live-overlays/unified-source/rotate").json()
    token = urlsplit(rotated["source_url"]).path.rsplit("/", 1)[-1]

    denied = client.get("/live-overlay/source/unified/not-the-token")
    assert denied.status_code == 404

    source = client.get(f"/live-overlay/source/unified/{token}")
    assert source.status_code == 200
    body = source.text
    assert "background:transparent" in body
    assert "function renderScene()" in body
    assert "function runEffect(" in body
    assert "max_effects_per_event" in body
    assert "/live-overlay/source/'+encodeURIComponent(TOKEN)+'/speech" in body
    assert "SpeechSynthesisUtterance" in body
    assert "eval(" not in body
    assert "new Function" not in body
    assert "<script src=" not in body
    assert source.headers["cache-control"] == "no-store"
    csp = source.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_control_room_is_no_store_and_explains_shared_secret(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    page = client.get("/live-overlay-studio/unified-source")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "private, no-store"
    assert "One LIVE source." in page.text
    assert "TikTok LIVE Studio" in page.text
    assert "OBS Browser Source" in page.text
    assert "Effect Packs" in page.text
    assert "rotation invalidates older Basic, Advanced, Effects and Unified source URLs" in page.text


def test_free_streamer_keeps_unified_effect_access_without_widening_paid_tools(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch, plan_id="free")
    assert client.get("/api/live-overlays/unified-source").status_code == 200
    effect_state = client.get("/api/live-overlays/effects")
    assert effect_state.status_code == 200
    assert effect_state.json()["all_effects_selectable"] is True
    timer_write = client.put("/api/live-overlays/timer", json={"name": "LIVE Timer", "seconds": 60, "running": True})
    assert timer_write.status_code == 403
