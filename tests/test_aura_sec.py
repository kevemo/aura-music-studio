from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec as aura_sec


def _user() -> dict:
    return {
        "id": "aura-sec-test-member",
        "display_name": "Security Member",
        "status": "active",
        "plan_id": "free",
        "requested_plan_id": "free",
    }


def _client(monkeypatch, *, signed_in: bool = True, dashboard: bool = False) -> TestClient:
    monkeypatch.setattr(
        aura_sec.accounts,
        "resolve_session",
        lambda _token: _user() if signed_in else None,
    )
    monkeypatch.setattr(
        aura_sec.security_store,
        "licence",
        lambda _user_id: {
            "user_id": _user()["id"],
            "status": "not_purchased",
            "sku_id": None,
            "device_limit": 0,
            "period_start": None,
            "period_end": None,
        },
    )
    monkeypatch.setattr(aura_sec.security_store, "list_devices", lambda _user_id: [])
    app = FastAPI()
    app.include_router(aura_sec.router)
    if dashboard:
        @app.get("/dashboard")
        def dashboard_route():
            return aura_sec._secure_html(
                "<html><body><div class='tools'><article class='tool'>Existing</article></div></body></html>"
            )

        app.add_middleware(aura_sec.AuraSecDashboardMiddleware)
    return TestClient(app)


def test_security_center_requires_member_session(monkeypatch):
    response = _client(monkeypatch, signed_in=False).get("/aura-sec", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin")


def test_security_center_uses_new_master_brand_and_truthful_foundation_state(monkeypatch):
    response = _client(monkeypatch).get("/aura-sec")
    assert response.status_code == 200
    text = response.text
    assert "Elevate Souls Productions Content Creation Command Center" in text
    assert "Powered by Aura AI" in text
    assert "Foundation status — native clients not released" in text
    assert "0 / 7" in text
    assert "Not Purchased" in text
    assert "No price has been invented or activated" in text
    assert "working title" in text.lower()
    assert "signed client is installed and verified" not in text
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"].startswith("no-store")


def test_status_never_reports_requesting_device_protected_from_web_session(monkeypatch):
    data = _client(monkeypatch).get("/api/aura-sec/status").json()
    assert data["master_product"] == "Elevate Souls Productions Content Creation Command Center"
    assert data["master_endorsement"] == "Powered by Aura AI"
    assert data["state"] == "foundation"
    assert data["separate_purchase"] is True
    assert data["included_in_creative_membership"] is False
    assert data["price_configured"] is False
    assert data["protection_active_on_this_device"] is False
    assert data["native_agents_released"] == 0
    assert data["absolute_security_guarantee"] is False
    assert data["commercial_name_clearance"] == "required_before_sale"
    assert data["licence"]["status"] == "not_purchased"
    assert data["device_health"]["overall"] == "not_enrolled"


def test_all_downloads_fail_closed_until_signed_release_exists(monkeypatch):
    data = _client(monkeypatch).get("/api/aura-sec/downloads").json()
    assert len(data["targets"]) == 7
    for target in data["targets"]:
        assert target["status"] == "not_released"
        assert target["signed_manifest_url"] is None
        assert target["download_url"] is None


def test_capability_catalog_covers_prevention_detection_recovery_and_optimisation(monkeypatch):
    data = _client(monkeypatch).get("/api/aura-sec/capabilities").json()
    ids = {domain["id"] for domain in data["domains"]}
    assert {
        "endpoint-edr",
        "ransomware-recovery",
        "web-scam",
        "network-vpn",
        "identity-auth",
        "vulnerability-patching",
        "privacy-permissions",
        "vault-backup",
        "storage-optimizer",
        "home-network",
        "incident-response",
        "product-integrity",
    } <= ids
    assert any("No custom cryptography" in item for item in data["principles"])
    assert any("Truthful capability reporting" in item for item in data["principles"])


def test_dashboard_middleware_adds_separate_security_entry_once(monkeypatch):
    client = _client(monkeypatch, dashboard=True)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.text.count("data-aura-sec-card='1'") == 1
    assert "Open Security Center" in response.text
    assert "Separate security purchase" in response.text
    assert "ESP Content Creation Command Center" in response.text
