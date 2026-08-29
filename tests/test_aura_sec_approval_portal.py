from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_approval_portal as portal
from aura_music_studio.aura_sec_approval_write_guard import AuraSecApprovalWriteGuardMiddleware


USER = {
    "id": "approval-portal-user",
    "email": "approval.portal@example.test",
    "display_name": "Approval Portal User",
    "status": "active",
    "plan_id": "free",
}

SAME_ORIGIN_HEADERS = {
    "Origin": "http://testserver",
    "Sec-Fetch-Site": "same-origin",
}


class FakeAccounts:
    def resolve_session(self, token):
        return dict(USER) if token == "portal-session-token" else None


class FakeSecurity:
    def get_action(self, _user_id, action_id):
        if action_id == "normal-action":
            return {
                "id": action_id,
                "device_id": "device-1",
                "action_type": "isolate_network",
                "risk_class": "confirmation_required",
                "status": "proposed",
                "details": {"summary": "Isolate network to contain verified suspicious traffic."},
            }
        if action_id == "wipe-action":
            return {
                "id": action_id,
                "device_id": "device-1",
                "action_type": "remote_wipe",
                "risk_class": "strong_reauth_required",
                "status": "proposed",
                "details": {"summary": "High-risk remote wipe recommendation."},
            }
        raise ValueError("not found")


class FakeApprovals:
    def __init__(self):
        self.challenge_calls = []
        self.approve_calls = []

    def create_challenge(self, user_id, action_id, *, session_token):
        self.challenge_calls.append((user_id, action_id, session_token))
        strong = action_id == "wipe-action"
        return {
            "challenge_id": f"challenge-{action_id}",
            "approval_token": f"approval-token-{action_id}-123456",
            "action_id": action_id,
            "risk_class": "strong_reauth_required" if strong else "confirmation_required",
            "strong_reauthentication_required": strong,
            "expires_at": "2026-08-27T06:00:00+00:00",
            "one_time": True,
            "command_issued": False,
        }

    def approve(self, user_id, action_id, *, session_token, approval_token, password=None):
        self.approve_calls.append((user_id, action_id, session_token, approval_token, password))
        return {
            "approved": True,
            "action": {
                "id": action_id,
                "action_type": "remote_wipe" if action_id == "wipe-action" else "isolate_network",
                "status": "approved",
            },
            "strong_reauthentication_verified": bool(password),
            "command_issued": False,
            "truth": "Approval recorded; a signed native-device session is still required.",
        }


def _client(monkeypatch, *, signed_in=True):
    accounts = FakeAccounts()
    if not signed_in:
        accounts.resolve_session = lambda _token: None
    approvals = FakeApprovals()
    monkeypatch.setattr(portal, "accounts", accounts)
    monkeypatch.setattr(portal, "security", FakeSecurity())
    monkeypatch.setattr(portal, "approvals", approvals)
    app = FastAPI()
    app.include_router(portal.router)
    app.add_middleware(AuraSecApprovalWriteGuardMiddleware)
    client = TestClient(app)
    client.cookies.set(portal.MEMBER_COOKIE, "portal-session-token")
    return client, approvals


def test_opening_review_page_is_read_only_and_creates_no_challenge(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.get("/aura-sec/approval/normal-action")
    assert response.status_code == 200
    assert approvals.challenge_calls == []
    assert "Nothing executes from this page" in response.text
    assert "Begin secure approval" in response.text
    assert response.headers["X-Frame-Options"] == "DENY"


def test_starting_normal_approval_creates_one_time_challenge_without_password_field(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/normal-action/start",
        headers=SAME_ORIGIN_HEADERS,
    )
    assert response.status_code == 200
    assert approvals.challenge_calls == [
        (USER["id"], "normal-action", "portal-session-token")
    ]
    assert "one action" in response.text
    assert "Approval is not execution" in response.text
    assert "type='password'" not in response.text


def test_high_risk_approval_start_requires_password_field(monkeypatch):
    client, _approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/wipe-action/start",
        headers=SAME_ORIGIN_HEADERS,
    )
    assert response.status_code == 200
    assert "Strong Reauth Required" in response.text
    assert "Re-enter your account password" in response.text
    assert "type='password'" in response.text


def test_confirm_approval_records_only_approval_not_execution(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/wipe-action/confirm",
        headers=SAME_ORIGIN_HEADERS,
        data={
            "approval_token": "approval-token-wipe-action-123456",
            "password": "member-password-value",
        },
    )
    assert response.status_code == 200
    assert approvals.approve_calls == [
        (
            USER["id"],
            "wipe-action",
            "portal-session-token",
            "approval-token-wipe-action-123456",
            "member-password-value",
        )
    ]
    assert "Action Approved" in response.text
    assert "No command was issued by this form" in response.text


def test_cross_origin_portal_post_cannot_mint_approval_challenge(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/normal-action/start",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.json()["command_issued"] is False
    assert approvals.challenge_calls == []


def test_approval_pages_require_login(monkeypatch):
    client, _approvals = _client(monkeypatch, signed_in=False)
    response = client.get("/aura-sec/approval/normal-action", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin?next=")
