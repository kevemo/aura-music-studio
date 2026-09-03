from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.member_dashboard as dashboard
from aura_music_studio.native_access import EffectiveNativeAccess


def _client(monkeypatch, membership):
    user = {
        "id": "member-unified-navigation",
        "display_name": "Unified Member",
        "status": "active",
        "plan_id": "free",
        "requested_plan_id": "free",
    }
    monkeypatch.setattr(dashboard.accounts, "resolve_session", lambda _cookie: user)
    monkeypatch.setattr(dashboard.esp, "membership", lambda _user_id: membership)
    monkeypatch.setattr(
        dashboard.native_access,
        "resolve",
        lambda user_id: EffectiveNativeAccess(
            user_id=user_id,
            membership_plan_id="free",
            membership_entitlements=frozenset(),
            purchased_entitlements=frozenset(),
        ),
    )
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app)


def test_regular_member_sees_all_shared_creative_houses_without_private_esp_shortcuts(monkeypatch):
    response = _client(monkeypatch, None).get("/dashboard")
    assert response.status_code == 200
    text = response.text

    assert "href='/studio'" in text
    assert "href='/video-studio'" in text
    assert "href='/image-designer'" in text
    assert "href='/game-creation'" in text
    assert "Open Game Forge" in text
    assert "href='/creative/library'" in text
    assert "href='/aura-intelligence'" in text
    assert "href='/aura-sec'" in text

    assert "href='/command-center/social'" not in text
    assert "href='/live-overlay-studio'" not in text
    assert "href='/command-center/agent/operations'" not in text
    assert "href='/command-center/agent/health'" not in text
    assert "href='/owner/dashboard'" not in text


def test_creator_navigation_exposes_creator_live_social_and_progress_but_not_agent_or_owner(monkeypatch):
    response = _client(monkeypatch, {"status": "active", "roles": "creator"}).get("/dashboard")
    assert response.status_code == 200
    text = response.text

    assert "href='/command-center'" in text
    assert "href='/command-center/social'" in text
    assert "href='/live-overlay-studio'" in text
    assert "href='/command-center/level-up'" in text
    assert "href='/command-center/progress'" in text
    assert "href='/command-center/agent/operations'" not in text
    assert "href='/command-center/agent/health'" not in text
    assert "href='/owner/dashboard'" not in text


def test_agent_navigation_exposes_agent_operations_without_creator_progress_or_owner(monkeypatch):
    response = _client(monkeypatch, {"status": "active", "roles": "agent"}).get("/dashboard")
    assert response.status_code == 200
    text = response.text

    assert "href='/command-center'" in text
    assert "href='/command-center/social'" in text
    assert "href='/live-overlay-studio'" in text
    assert "href='/command-center/agent/operations'" in text
    assert "href='/command-center/agent/health'" in text
    assert "href='/command-center/progress'" not in text
    assert "href='/owner/dashboard'" not in text


def test_both_role_combines_creator_and_agent_navigation(monkeypatch):
    response = _client(monkeypatch, {"status": "active", "roles": "both"}).get("/dashboard")
    assert response.status_code == 200
    text = response.text

    assert "href='/command-center/progress'" in text
    assert "href='/command-center/agent/operations'" in text
    assert "href='/command-center/agent/health'" in text
    assert "href='/owner/dashboard'" not in text


def test_owner_navigation_exposes_full_private_navigation(monkeypatch):
    response = _client(monkeypatch, {"status": "owner", "roles": ""}).get("/dashboard")
    assert response.status_code == 200
    text = response.text

    assert "ESP Owner" in text
    assert "href='/command-center/progress'" in text
    assert "href='/command-center/agent/operations'" in text
    assert "href='/command-center/agent/health'" in text
    assert "href='/owner/dashboard'" in text
    assert "Owner Command Center" in text


def test_private_dashboard_sets_no_store_and_no_referrer(monkeypatch):
    response = _client(monkeypatch, {"status": "active", "roles": "creator"}).get("/dashboard")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
