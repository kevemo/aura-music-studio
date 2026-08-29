from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_portal as portal


USER = {"id": "portal-user", "display_name": "Portal User", "status": "active", "plan_id": "free"}


class FakeAccounts:
    def resolve_session(self, token):
        return USER if token else None


class FakeSecurity:
    def __init__(self, *, licence_status="not_purchased", device_limit=0, devices=None):
        self.licence_status = licence_status
        self.device_limit = device_limit
        self.devices = list(devices or [])

    def licence(self, _user_id):
        return {"status": self.licence_status, "device_limit": self.device_limit, "sku_id": None}

    def list_devices(self, _user_id):
        return list(self.devices)


class FakeRecovery:
    def readiness(self, _user_id, _device_id):
        raise AssertionError("readiness should not run without devices")


class FakeReadModel:
    def __init__(self, *, incident=None, incident_actions=None):
        self._incident = incident
        self._incident_actions = list(incident_actions or [])

    def counts(self, _user_id):
        return {
            "incidents": {"total": 0, "open": 0, "urgent": 0},
            "actions": {"total": 0, "awaiting_approval": 0, "verified": 0},
            "recovery": {"targets": 0, "encrypted_targets": 0, "isolated_targets": 0},
        }

    def incidents(self, _user_id, *, limit=100):
        return [] if self._incident is None else [self._incident]

    def incident(self, _user_id, incident_id):
        if self._incident and self._incident.get("id") == incident_id:
            return dict(self._incident)
        return None

    def actions(self, _user_id, *, limit=100):
        return list(self._incident_actions)

    def actions_for_incident(self, _user_id, incident_id, *, limit=100):
        return [item for item in self._incident_actions if item.get("incident_id") == incident_id]

    def backup_targets(self, _user_id, *, device_id=None):
        return []

    def recovery_points(self, _user_id, *, device_id=None, limit=100):
        return []

    def restore_drills(self, _user_id, *, device_id=None, limit=100):
        return []


class FakeVulnerabilities:
    def __init__(self, findings=None):
        self.findings = list(findings or [])

    def list(self, _user_id, *, device_id=None, status="open", limit=250):
        return [item for item in self.findings if not status or item.get("status") == status][:limit]

    def get(self, _user_id, finding_id):
        for item in self.findings:
            if item.get("id") == finding_id:
                return dict(item)
        raise ValueError("Aura Sec vulnerability finding not found")


def _client(
    monkeypatch,
    *,
    signed_in=True,
    security=None,
    read_model=None,
    vulnerabilities=None,
):
    accounts = FakeAccounts()
    if not signed_in:
        accounts.resolve_session = lambda _token: None
    monkeypatch.setattr(portal, "accounts", accounts)
    monkeypatch.setattr(portal, "security", security or FakeSecurity())
    monkeypatch.setattr(portal, "recovery", FakeRecovery())
    monkeypatch.setattr(portal, "read_model", read_model or FakeReadModel())
    monkeypatch.setattr(portal, "vulnerabilities", vulnerabilities or FakeVulnerabilities())
    monkeypatch.delenv("AURA_SEC_SKUS_JSON", raising=False)
    app = FastAPI()
    app.include_router(portal.router)
    return TestClient(app)


def _get(client: TestClient, path: str):
    return client.get(path, cookies={portal.MEMBER_COOKIE: "session"})


def test_all_member_security_pages_require_authentication(monkeypatch):
    client = _client(monkeypatch, signed_in=False)
    for path in (
        "/aura-sec/devices",
        "/aura-sec/enrol",
        "/aura-sec/threats",
        "/aura-sec/vulnerabilities",
        "/aura-sec/recovery",
        "/aura-sec/optimizer",
        "/aura-sec/downloads",
        "/aura-sec/plans",
        "/aura-sec/settings",
        "/aura-sec/incident/missing",
        "/aura-sec/vulnerability/missing",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/signin?next=")


def test_security_pages_share_command_center_brand_and_expanded_navigation(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/devices")
    assert response.status_code == 200
    text = response.text
    assert "Elevate Souls Productions Content Creation Command Center" in text
    assert "Powered by Aura AI" in text
    assert "Enrol Device" in text
    assert "Threats &amp; Incidents" in text
    assert "Vulnerabilities" in text
    assert "Plans &amp; Licence" in text
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


def test_enrolment_page_has_no_browser_attestation_shortcut(monkeypatch):
    client = _client(monkeypatch, security=FakeSecurity(licence_status="active", device_limit=3))
    response = _get(client, "/aura-sec/enrol")
    assert response.status_code == 200
    assert "3 device slot(s) available" in response.text
    assert "Browser-only enrolment is deliberately unavailable" in response.text
    assert "no checkbox or form that can pretend device proof was verified" in response.text
    assert "signed native client" in response.text


def test_threat_page_refuses_to_fabricate_activity(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/threats")
    assert response.status_code == 200
    assert "No verified security incidents recorded" in response.text
    assert "will not fabricate threats" in response.text
    assert "No remediation actions recorded" in response.text


def test_incident_detail_is_member_scoped_and_has_no_generic_execute_control(monkeypatch):
    incident = {
        "id": "incident-1",
        "device_id": "device-1",
        "severity": "critical",
        "status": "open",
        "title": "Ransomware-like writes",
        "detection_id": "AURA-RANSOM-1",
        "confidence": 0.98,
        "summary": {"observed_files": 12, "source": "verified native telemetry"},
        "created_at": "2026-08-27T04:00:00+00:00",
        "updated_at": "2026-08-27T04:01:00+00:00",
        "display_name": "Windows PC",
        "platform": "windows",
        "architecture": "x64",
        "agent_version": "0.1.0",
        "protection_state": "isolated",
        "last_seen_at": "2026-08-27T04:01:00+00:00",
    }
    action = {
        "id": "action-1",
        "incident_id": "incident-1",
        "device_id": "device-1",
        "action_type": "isolate_network",
        "risk_class": "confirmation_required",
        "status": "proposed",
        "requested_at": "2026-08-27T04:01:00+00:00",
        "approved_at": None,
        "executed_at": None,
        "verified_at": None,
        "display_name": "Windows PC",
        "platform": "windows",
    }
    client = _client(monkeypatch, read_model=FakeReadModel(incident=incident, incident_actions=[action]))
    response = _get(client, "/aura-sec/incident/incident-1")
    assert response.status_code == 200
    assert "Ransomware-like writes" in response.text
    assert "verified native telemetry" in response.text
    assert "Aura Security Guide" in response.text
    assert "cannot invent a successful containment" in response.text
    assert "Isolate Network" in response.text
    assert ">Execute<" not in response.text

    missing = _get(client, "/aura-sec/incident/not-this-member")
    assert missing.status_code == 404


def test_vulnerability_pages_show_verified_empty_state_and_advisory_patch_boundary(monkeypatch):
    empty = _get(_client(monkeypatch), "/aura-sec/vulnerabilities")
    assert empty.status_code == 200
    assert "No open verified vulnerabilities are recorded" in empty.text
    assert "browser does not infer installed software" in empty.text

    finding = {
        "id": "finding-1",
        "user_id": USER["id"],
        "device_id": "device-1",
        "cve_id": "CVE-2026-55555",
        "product": "Example Service",
        "installed_version": "1.0",
        "fixed_version": "1.1",
        "cvss_base": 9.8,
        "epss_probability": 0.91,
        "cisa_kev": True,
        "exposure": "internet",
        "privilege_required": "none",
        "asset_sensitivity": "critical",
        "patch_available": True,
        "exploit_observed_locally": False,
        "vulnerable_feature_enabled": True,
        "priority_score": 100,
        "priority": "emergency",
        "reasons": ["CISA KEV indicates evidence of exploitation in the wild."],
        "inventory_digest": "a" * 64,
        "intelligence_digest": "b" * 64,
        "status": "open",
        "observed_at": "2026-08-27T04:00:00+00:00",
        "last_verified_at": "2026-08-27T04:01:00+00:00",
        "resolved_at": None,
        "display_name": "Windows PC",
        "platform": "windows",
    }
    client = _client(monkeypatch, vulnerabilities=FakeVulnerabilities([finding]))
    detail = _get(client, "/aura-sec/vulnerability/finding-1")
    assert detail.status_code == 200
    assert "CVE-2026-55555" in detail.text
    assert "Known exploited" in detail.text
    assert "Remediation remains approval-gated" in detail.text
    assert "cannot call this resolved until the native client verifies" in detail.text
    assert ">Patch now<" not in detail.text

    missing = _get(client, "/aura-sec/vulnerability/not-this-member")
    assert missing.status_code == 404


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


def test_plans_page_invents_neither_price_nor_checkout(monkeypatch):
    client = _client(monkeypatch)
    response = _get(client, "/aura-sec/plans")
    assert response.status_code == 200
    assert "Aura Sec is not yet available for purchase" in response.text
    assert "does not invent a price" in response.text
    assert "verified checkout enabled" in response.text
    assert "Free, Basic and Pro" in response.text


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
