from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_portal as portal


USER = {"id": "portal-user", "display_name": "Portal User", "status": "active", "plan_id": "free"}


class FakeAccounts:
    def resolve_session(self, token):
        return USER if token else None


class FakeSecurity:
    def licence(self, _user_id):
        return {"status": "not_purchased", "device_limit": 0, "sku_id": None}

    def list_devices(self, _user_id):
        return []


class FakeRecovery:
    def readiness(self, _user_id, _device_id):
        raise AssertionError("readiness should not run without devices")


class FakeReadModel:
    def counts(self, _user_id):
        return {
            "incidents": {"total": 0, "open": 0, "urgent": 0},
            "actions": {"total": 0, "awaiting_approval": 0, "verified": 0},
            "recovery": {"targets": 0, "encrypted_targets": 0, "isolated_targets": 0},
        }

    def incidents(self, _user_id, *, limit=100):
        return []

    def actions(self, _user_id, *, limit=100):
        return []

    def backup_targets(self, _user_id, *, device_id=None):
        return []

    def recovery_points(self, _user_id, *, device_id=None, limit=100):
        return []

    def restore_drills(self, _user_id, *, device_id=None, limit=100):
        return []


def _client(monkeypatch, *, signed_in=True):
    accounts = FakeAccounts()
    if not signed_in:
        accounts.resolve_session = lambda _token: None
    monkeypatch.setattr(portal, "accounts", accounts)
    monkeypatch.setattr(portal, "security", FakeSecurity())
    monkeypatch.setattr(portal, "recovery", FakeRecovery())
    monkeypatch.setattr(portal, "read_model", FakeReadModel())
    app = FastAPI()
    app.include_router(portal.router)
    return TestClient(app)


def _get(client: TestClient, path: str):
    return client.get(path, cookies={portal.MEMBER_COOKIE: "session"})


def test_all_member_security_pages_require_authentication(monkeypatch):
    client = _client(monkeypatch, signed_in=False)
    for path in (
        "/aura-sec/devices",
        "/aura-sec/threats",
        "/aura-sec/recovery",
        "/aura-sec/optimizer",
        "/aura-sec/downloads",
        "/aura-sec/settings",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/signin?next=")


def test_security_pages_share_command_center_brand_and_navigation(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/devices")
    assert response.status_code == 200
    text = response.text
    assert "Elevate Souls Productions Content Creation Command Center" in text
    assert "Powered by Aura AI" in text
    assert "Threats &amp; Incidents" in text
    assert "Protection Downloads" in text
    assert "Security Settings" in text
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"].startswith("no-store")


def test_devices_page_does_not_claim_browser_is_protected(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/devices")
    assert response.status_code == 200
    assert "No enrolled devices yet" in response.text
    assert "A website session is not an endpoint enrolment" in response.text
    assert "currently verified healthy" in response.text


def test_threat_page_refuses_to_fabricate_activity(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/threats")
    assert response.status_code == 200
    assert "No verified security incidents recorded" in response.text
    assert "will not fabricate threats" in response.text
    assert "No remediation actions recorded" in response.text


def test_optimizer_page_reports_zero_until_native_evidence_exists(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/optimizer")
    assert response.status_code == 200
    assert "0 B" in response.text
    assert "No native optimiser report has been received yet" in response.text
    assert "rather than inventing a cleanup figure" in response.text


def test_download_page_keeps_all_native_packages_locked(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/downloads")
    assert response.status_code == 200
    assert response.text.count("Not released") == 7
    assert "verified signed release manifest" in response.text
    assert "SBOM" in response.text


def test_security_center_api_is_read_only_truthful_summary(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/api/aura-sec/member/security-center")
    assert response.status_code == 200
    data = response.json()
    assert data["licence"]["status"] == "not_purchased"
    assert data["health"]["overall"] == "not_enrolled"
    assert data["counts"]["incidents"]["total"] == 0
    assert data["recovery"] == []
    assert "read-only" in data["truth"]
