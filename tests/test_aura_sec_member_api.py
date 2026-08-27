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


def _client(
    monkeypatch,
    *,
    licence_status="not_purchased",
    devices=None,
    findings=None,
    incidents=None,
    actions=None,
):
    findings = list(findings or [])
    incidents = list(incidents or [])
    actions = list(actions or [])
    monkeypatch.setattr(member_api.accounts, "resolve_session", lambda _token: USER)
    monkeypatch.setattr(
        member_api.security,
        "licence",
        lambda _user_id: {
            "user_id": USER["id"],
            "status": licence_status,
            "sku_id": "aura-sec-test" if licence_status == "active" else None,
            "device_limit": 3 if licence_status == "active" else 0,
            "period_start": NOW if licence_status == "active" else None,
            "period_end": NOW if licence_status == "active" else None,
        },
    )
    monkeypatch.setattr(member_api.security, "list_devices", lambda _user_id: list(devices or []))
    monkeypatch.setattr(
        member_api.vulnerabilities,
        "list",
        lambda _user_id, status="open", limit=250: list(findings)[:limit],
    )

    def get_finding(_user_id, finding_id):
        for item in findings:
            if item.get("id") == finding_id:
                return dict(item)
        raise ValueError("Aura Sec vulnerability finding not found")

    monkeypatch.setattr(member_api.vulnerabilities, "get", get_finding)
    monkeypatch.setattr(member_api.read_model, "incidents", lambda _user_id, limit=250: list(incidents)[:limit])
    monkeypatch.setattr(
        member_api.read_model,
        "incident",
        lambda _user_id, incident_id: next((dict(item) for item in incidents if item.get("id") == incident_id), None),
    )
    monkeypatch.setattr(
        member_api.read_model,
        "actions_for_incident",
        lambda _user_id, incident_id, limit=250: [
            dict(item) for item in actions if item.get("incident_id") == incident_id
        ][:limit],
    )
    monkeypatch.setattr(
        member_api.read_model,
        "counts",
        lambda _user_id: {
            "incidents": {
                "total": len(incidents),
                "open": sum(1 for item in incidents if item.get("status") == "open"),
                "urgent": sum(
                    1
                    for item in incidents
                    if item.get("status") == "open" and item.get("severity") in {"high", "critical"}
                ),
            },
            "actions": {"total": len(actions), "awaiting_approval": 0, "verified": 0},
            "recovery": {"targets": 0, "encrypted_targets": 0, "isolated_targets": 0},
        },
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


def test_enrolment_readiness_never_claims_browser_can_attest_device(monkeypatch):
    response = _client(monkeypatch, licence_status="active").get(
        "/api/aura-sec/member/enrolment-readiness"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["device_limit"] == 3
    assert data["remaining_device_slots"] == 3
    assert data["can_request_native_enrolment"] is True
    assert data["browser_can_complete_device_attestation"] is False
    assert data["signed_native_client_released"] is False


def test_verified_incident_detail_is_read_only_and_member_scoped(monkeypatch):
    incidents = [
        {
            "id": "incident-1",
            "severity": "critical",
            "status": "open",
            "title": "Verified ransomware signal",
        }
    ]
    actions = [
        {
            "id": "action-1",
            "incident_id": "incident-1",
            "status": "proposed",
            "action_type": "isolate_network",
        },
        {
            "id": "action-2",
            "incident_id": "incident-1",
            "status": "verified",
            "action_type": "run_full_scan",
        },
    ]
    client = _client(monkeypatch, incidents=incidents, actions=actions)
    response = client.get("/api/aura-sec/member/incidents/incident-1")
    assert response.status_code == 200
    data = response.json()
    assert data["incident"]["title"] == "Verified ransomware signal"
    assert data["response_summary"]["linked_actions"] == 2
    assert data["response_summary"]["proposed"] == 1
    assert data["response_summary"]["verified"] == 1
    assert "read-only" in data["truth"]
    assert client.get("/api/aura-sec/member/incidents/not-owned").status_code == 404


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


def test_verified_vulnerability_detail_never_grants_automatic_patch_permission(monkeypatch):
    finding = {
        "id": "finding-1",
        "priority": "emergency",
        "priority_score": 100,
        "cisa_kev": True,
        "product": "Example Service",
        "installed_version": "1.0",
        "fixed_version": "1.1",
    }
    client = _client(monkeypatch, findings=[finding])
    response = client.get("/api/aura-sec/member/vulnerabilities/finding-1")
    assert response.status_code == 200
    data = response.json()
    assert data["finding"]["fixed_version"] == "1.1"
    assert data["automatic_patch_permission"] is False
    assert data["resolution_requires_verified_fixed_version"] is True
    assert client.get("/api/aura-sec/member/vulnerabilities/not-owned").status_code == 404


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
