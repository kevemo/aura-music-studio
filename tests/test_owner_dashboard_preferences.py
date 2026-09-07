from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.esp_owner_access_portal as access_portal
import aura_music_studio.owner_dashboard_preferences_portal as portal
from aura_music_studio.owner_dashboard_preferences import (
    DASHBOARD_WIDGETS,
    OwnerDashboardPreferenceStore,
)


def test_mary_and_kev_have_independent_default_widget_orders(tmp_path: Path):
    store = OwnerDashboardPreferenceStore(tmp_path / "owners.sqlite3")
    mary = store.get("mary")
    kev = store.get("kev")
    assert mary.layout_mode == "executive"
    assert kev.layout_mode == "executive"
    assert mary.widget_order != kev.widget_order
    assert set(mary.widget_order) == set(DASHBOARD_WIDGETS)
    assert set(kev.widget_order) == set(DASHBOARD_WIDGETS)


def test_saving_mary_preferences_does_not_change_kev(tmp_path: Path, monkeypatch):
    store = OwnerDashboardPreferenceStore(tmp_path / "owners.sqlite3")
    monkeypatch.setattr(
        "aura_music_studio.owner_dashboard_preferences.owner_actor",
        lambda _fallback="": "Mary · ESP Owner",
    )
    kev_before = store.get("kev")
    saved = store.save(
        "mary",
        layout_mode="network",
        density="compact",
        default_route="/owner/users",
        widget_order=["esp_network", "creator_development", "executive_summary"],
        hidden_widgets=["support", "finance", "not-a-widget"],
    )
    assert saved.layout_mode == "network"
    assert saved.density == "compact"
    assert saved.default_route == "/owner/users"
    assert saved.widget_order[:3] == (
        "esp_network",
        "creator_development",
        "executive_summary",
    )
    assert "support" in saved.hidden_widgets
    assert "finance" in saved.hidden_widgets
    assert "not-a-widget" not in saved.hidden_widgets
    assert saved.updated_by == "Mary · ESP Owner"
    assert store.get("kev") == kev_before
    audit = store.audit("mary")
    assert len(audit) == 1
    assert audit[0]["actor"] == "Mary · ESP Owner"


def test_invalid_dashboard_authority_values_are_rejected(tmp_path: Path):
    store = OwnerDashboardPreferenceStore(tmp_path / "owners.sqlite3")
    with pytest.raises(ValueError, match="layout"):
        store.save(
            "mary",
            layout_mode="super-admin",
            density="comfortable",
            default_route="/owner/dashboard",
            widget_order=DASHBOARD_WIDGETS,
            hidden_widgets=[],
        )
    with pytest.raises(ValueError, match="landing"):
        store.save(
            "mary",
            layout_mode="executive",
            density="comfortable",
            default_route="https://example.com",
            widget_order=DASHBOARD_WIDGETS,
            hidden_widgets=[],
        )
    with pytest.raises(ValueError, match="persona"):
        store.get("someone-else")


def test_aura_context_is_explicitly_presentation_only(tmp_path: Path):
    pref = OwnerDashboardPreferenceStore(tmp_path / "owners.sqlite3").get("kev")
    context = pref.aura_context()
    assert "presentation preferences only" in context
    assert "never change permissions" in context
    assert "financial controls" in context
    assert "ESP roles" in context
    assert "explicit owner action" in context


def test_personalized_dashboard_overlay_is_reachable_through_access_router(
    monkeypatch, tmp_path: Path
):
    store = OwnerDashboardPreferenceStore(tmp_path / "overlay.sqlite3")
    monkeypatch.setattr(portal, "preferences", store)
    monkeypatch.setattr(portal, "control", _FakeControl())
    monkeypatch.setattr(
        portal,
        "_credit_summary",
        lambda: {
            "wallets": 2,
            "credits_in_circulation": 40,
            "purchase_events": 1,
            "spend_events": 3,
        },
    )
    monkeypatch.setattr(portal, "owner_authorized", lambda _request: True)
    monkeypatch.setattr(portal, "request_owner_persona", lambda _request: "mary")
    app = FastAPI()
    app.include_router(access_portal.router)
    client = TestClient(app)
    response = client.get("/owner/dashboard")
    assert response.status_code == 200
    assert "Mary's Dashboard" in response.text
    assert "Customize dashboard" in response.text


class _FakeControl:
    db_path = Path("/tmp/nonexistent-owner-dashboard-test.sqlite3")

    def dashboard_summary(self):
        return {
            "users": 9,
            "regular": 3,
            "esp_pending": 1,
            "esp_creators": 4,
            "esp_agents": 2,
            "progress_submissions": 7,
            "plans": {"free": 4, "base": 3, "pro": 2},
        }

    def list_users(self):
        return [
            {
                "display_name": "Creator One",
                "esp_status": "active",
                "esp_roles": "creator",
                "last_progress_at": "2026-08-27T01:00:00+00:00",
                "progress_submissions": 3,
                "avg_training_percent": 80,
                "usage_events": 5,
                "project_count": 2,
            },
            {
                "display_name": "Agent One",
                "esp_status": "active",
                "esp_roles": "agent",
                "last_progress_at": None,
                "progress_submissions": 0,
                "avg_training_percent": 50,
                "usage_events": 2,
                "project_count": 1,
            },
        ]

    def audit_log(self, limit=8):
        return [
            {
                "action": "set_esp_role",
                "actor": "Mary · ESP Owner",
                "created_at": "2026-08-27T01:30:00+00:00",
            }
        ][:limit]


def _client(monkeypatch, tmp_path: Path, persona: str = "mary") -> TestClient:
    store = OwnerDashboardPreferenceStore(tmp_path / "portal.sqlite3")
    monkeypatch.setattr(portal, "preferences", store)
    monkeypatch.setattr(portal, "control", _FakeControl())
    monkeypatch.setattr(
        portal,
        "_credit_summary",
        lambda: {
            "wallets": 2,
            "credits_in_circulation": 40,
            "purchase_events": 1,
            "spend_events": 3,
        },
    )
    monkeypatch.setattr(portal, "owner_authorized", lambda _request: True)
    monkeypatch.setattr(portal, "request_owner_persona", lambda _request: persona)
    app = FastAPI()
    app.include_router(portal.router)
    return TestClient(app)


def test_personalized_dashboard_renders_selected_owner_and_truthful_finance(
    monkeypatch, tmp_path: Path
):
    client = _client(monkeypatch, tmp_path, "mary")
    response = client.get("/owner/dashboard")
    assert response.status_code == 200
    assert "Mary's Dashboard" in response.text
    assert "Customize dashboard" in response.text
    assert "Credit purchase events" in response.text
    assert "does not invent currency totals" in response.text
    assert "Credits are platform units only" in response.text


def test_hidden_widget_stays_hidden_after_explicit_save(monkeypatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path, "kev")
    portal.preferences.save(
        "kev",
        layout_mode="operations",
        density="compact",
        default_route="/owner/dashboard",
        widget_order=DASHBOARD_WIDGETS,
        hidden_widgets=["support"],
    )
    response = client.get("/owner/dashboard")
    assert response.status_code == 200
    assert "Kev's Dashboard" in response.text
    assert "layout-operations compact" in response.text
    assert "Owner Escalation" not in response.text
    assert "Agent Operations" in response.text


def test_owner_preference_context_endpoint_requires_selected_owner(
    monkeypatch, tmp_path: Path
):
    client = _client(monkeypatch, tmp_path, "mary")
    response = client.get("/owner/preferences/context")
    assert response.status_code == 200
    payload = response.json()
    assert payload["owner"] == "Mary"
    assert (
        payload["authority_note"]
        == "Presentation preferences do not grant or change permissions."
    )
    assert "never change permissions" in payload["aura_context"]
