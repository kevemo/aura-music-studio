from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_member_api as member_api


NOW = datetime.now(timezone.utc).isoformat()
USER = {
    "id": "member_api_security_user",
    "display_name": "Security Member",
    "status": "active",
    "plan_id": "pro",
    "requested_plan_id": "pro",
}


def _client(monkeypatch, *, licence_status="not_purchased", devices=None, findings=None):
    monkeypatch.setattr(member_api.accounts, "resolve_session", lambda _token: USER)
    monkeypatch.setattr(
        member_api.security,
        "licence",
        lambda _user_id: {
            "user_id": USER["id"],
            "status": licence_status,
            "sku_id": "security-test" if licence_status == "active" else None,
            "device_limit": 3 if licence_status == "active" else 0,
            "period_start": NOW if licence_status == "active" else None,
            "period_end": NOW if licence_status == "active" else None,
        },
    )
    monkeypatch.setattr(member_api.security, "list_devices", lambda _user_id: list(devices or []))
    monkeypatch.setattr(
        member_api.vulnerabilities,
        "list",
        lambda _user_id, status="open", limit=250: list(findings or []),
    )
    app = FastAPI()
    app.include_router(member_api.router)
    return TestClient(app)


def test_pro_creative_member_still_has_no_security_entitlement_without_purchase(monkeypatch):
    response = _client(monkeypatch).get("/api/aura-sec/member/licence")
    assert response.status_code == 200
    data = response.json()
    assert USER["plan_id"] == "pro"
    assert data["licence"]["status"] == "not_purchased"
    assert data["creative_membership_is_security_entitlement"] is False


def test_active_licence_without_enrolled_device_is_not_reported_protected(monkeypatch):
    response = _client(monkeypatch, licence_status="active").get("/api/aura-sec/member/overview")
    data = response.json()
    assert data["licence"]["status"] == "active"
    assert data["health"]["overall"] == "not_enrolled"
    assert data["at_least_one_device_currently_verified_healthy"] is False
    assert data["all_managed_devices_currently_verified_healthy"] is False


def test_active_licence_plus_recent_healthy_device_reports_verified_health(monkeypatch):
    devices = [
        {
            "id": "device_1234567890abcdef",
            "display_name": "Windows PC",
            "platform": "windows",
            "architecture": "x64",
            "agent_version": "0.1.0",
            "status": "enrolled",
            "protection_state": "healthy",
            "enrolled_at": NOW,
            "last_seen_at": NOW,
            "last_policy_version": "policy-1",
            "revoked_at": None,
        }
    ]
    data = _client(monkeypatch, licence_status="active", devices=devices).get(
        "/api/aura-sec/member/overview"
    ).json()
    assert data["health"]["overall"] == "healthy"
    assert data["at_least_one_device_currently_verified_healthy"] is True
    assert data["all_managed_devices_currently_verified_healthy"] is True


def test_verified_vulnerability_list_reports_only_persisted_native_findings(monkeypatch):
    findings = [
        {
            "id": "finding-1",
            "priority": "emergency",
            "priority_score": 100,
            "cisa_kev": True,
            "product": "Example Service",
            "installed_version": "1.0",
        },
        {
            "id": "finding-2",
            "priority": "high",
            "priority_score": 70,
            "cisa_kev": False,
            "product": "Example Browser",
            "installed_version": "2.0",
        },
    ]
    response = _client(monkeypatch, findings=findings).get("/api/aura-sec/member/vulnerabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["critical_or_emergency"] == 1
    assert data["known_exploited"] == 1
    assert "verified native inventory" in data["truth"]


def test_vulnerability_priority_endpoint_is_advisory_not_auto_patch(monkeypatch):
    payload = {
        "cve_id": "CVE-2026-55555",
        "product": "Example Service",
        "installed_version": "1.0",
        "fixed_version": "1.1",
        "cvss_base": 8.8,
        "epss_probability": 0.9,
        "cisa_kev": True,
        "exposure": "internet",
        "privilege_required": "none",
        "asset_sensitivity": "critical",
        "patch_available": True,
        "exploit_observed_locally": False,
        "vulnerable_feature_enabled": True,
    }
    response = _client(monkeypatch).post("/api/aura-sec/member/vulnerability-priority", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["priority"] == "emergency"
    assert data["auto_patch_allowed"] is False
    assert data["confirmation_required"] is True
