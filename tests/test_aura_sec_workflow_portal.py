from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_workflow_portal as workflow


NOW = datetime.now(timezone.utc).isoformat()
USER = {"id": "workflow-user", "display_name": "Workflow User", "status": "active", "plan_id": "free"}
DEVICE = {
    "id": "device_workflow_123456",
    "display_name": "Studio PC",
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


class FakeAccounts:
    def resolve_session(self, token):
        return USER if token else None


class FakeSecurity:
    def get_device(self, _user_id, device_id):
        if device_id != DEVICE["id"]:
            raise ValueError("not found")
        return dict(DEVICE)

    def list_devices(self, _user_id):
        return [dict(DEVICE)]


class FakeReadModel:
    def incidents(self, _user_id, *, limit=100):
        return [
            {
                "id": "incident-workflow-1",
                "device_id": DEVICE["id"],
                "title": "Verified suspicious encryption behaviour",
                "severity": "high",
                "status": "open",
                "created_at": NOW,
            }
        ]

    def actions(self, _user_id, *, limit=100):
        return [
            {
                "id": "action-workflow-1",
                "incident_id": "incident-workflow-1",
                "device_id": DEVICE["id"],
                "display_name": DEVICE["display_name"],
                "action_type": "isolate_network",
                "risk_class": "confirmation_required",
                "status": "proposed",
                "requested_at": NOW,
            },
            {
                "id": "action-workflow-2",
                "incident_id": None,
                "device_id": DEVICE["id"],
                "display_name": DEVICE["display_name"],
                "action_type": "remote_wipe",
                "risk_class": "strong_reauth_required",
                "status": "proposed",
                "requested_at": NOW,
            },
        ]

    def restore_drills(self, _user_id, *, device_id=None, limit=100):
        return []


class FakeRecovery:
    def readiness(self, _user_id, _device_id):
        return {
            "state": "restore_test_required",
            "active_targets": 1,
            "restore_proven": False,
            "truth": "Backup exists, but a successful restore drill has not yet proved recovery.",
        }


class FakeVulnerabilities:
    def list(self, _user_id, *, device_id=None, status="open", limit=250):
        return [
            {
                "id": "finding-workflow-1",
                "device_id": DEVICE["id"],
                "cve_id": "CVE-2026-60001",
                "product": "Example Runtime",
                "installed_version": "1.0",
                "priority_score": 88,
                "priority": "emergency",
            }
        ]


def _client(monkeypatch, *, signed_in=True):
    accounts = FakeAccounts()
    if not signed_in:
        accounts.resolve_session = lambda _token: None
    monkeypatch.setattr(workflow, "accounts", accounts)
    monkeypatch.setattr(workflow, "security", FakeSecurity())
    monkeypatch.setattr(workflow, "read_model", FakeReadModel())
    monkeypatch.setattr(workflow, "recovery", FakeRecovery())
    monkeypatch.setattr(workflow, "vulnerabilities", FakeVulnerabilities())
    app = FastAPI()
    app.include_router(workflow.router)
    return TestClient(app)


def _get(client: TestClient, path: str):
    return client.get(path, cookies={workflow.MEMBER_COOKIE: "session"})


def test_workflow_pages_require_member_authentication(monkeypatch):
    client = _client(monkeypatch, signed_in=False)
    for path in (
        f"/aura-sec/device/{DEVICE['id']}",
        "/aura-sec/approvals",
        "/aura-sec/recovery/drill",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/signin?next=")


def test_device_detail_is_read_only_and_links_verified_evidence(monkeypatch):
    response = _get(_client(monkeypatch), f"/aura-sec/device/{DEVICE['id']}")
    assert response.status_code == 200
    assert "Studio PC" in response.text
    assert "CVE-2026-60001" in response.text
    assert "Verified suspicious encryption behaviour" in response.text
    assert "Read-only device view" in response.text
    assert "approve remediation" in response.text
    assert "issue a command" in response.text
    assert response.headers["X-Frame-Options"] == "DENY"


def test_unknown_or_cross_member_device_is_not_rendered(monkeypatch):
    response = _get(_client(monkeypatch), "/aura-sec/device/not-owned-device")
    assert response.status_code == 404


def test_approvals_center_has_no_generic_execute_control(monkeypatch):
    response = _get(_client(monkeypatch), "/aura-sec/approvals")
    assert response.status_code == 200
    assert "Isolate Network" in response.text
    assert "Remote Wipe" in response.text
    assert "requiring strong re-authentication" in response.text
    assert "browser-executable generic commands" in response.text
    assert ">Execute<" not in response.text
    assert "Execution is intentionally locked here" in response.text


def test_restore_drill_page_keeps_launch_disabled_until_native_workflow_exists(monkeypatch):
    response = _get(_client(monkeypatch), "/aura-sec/recovery/drill")
    assert response.status_code == 200
    assert "Restore Test Required" in response.text
    assert "Start drill — native workflow not released" in response.text
    assert "No browser-only restore" in response.text
    assert "strong re-authentication" in response.text
