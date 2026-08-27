from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_member_api as member_api


USER = {
    "id": "approval-api-user",
    "email": "approval.api@example.test",
    "display_name": "Approval API User",
    "status": "active",
    "plan_id": "free",
}


class FakeAccounts:
    def resolve_session(self, token):
        return dict(USER) if token == "approval-session-token" else None


class FakeApprovals:
    def __init__(self):
        self.challenge_calls = []
        self.approve_calls = []

    def create_challenge(self, user_id, action_id, *, session_token):
        self.challenge_calls.append((user_id, action_id, session_token))
        return {
            "challenge_id": "challenge-1",
            "approval_token": "approval-token-1234567890",
            "action_id": action_id,
            "risk_class": "confirmation_required",
            "strong_reauthentication_required": False,
            "expires_at": "2026-08-27T06:00:00+00:00",
            "one_time": True,
            "command_issued": False,
        }

    def approve(self, user_id, action_id, *, session_token, approval_token, password=None):
        self.approve_calls.append((user_id, action_id, session_token, approval_token, password))
        return {
            "approved": True,
            "action": {"id": action_id, "status": "approved"},
            "strong_reauthentication_verified": bool(password),
            "command_issued": False,
            "truth": "Approved only; no native command issued.",
        }


def _client(monkeypatch):
    approvals = FakeApprovals()
    monkeypatch.setattr(member_api, "accounts", FakeAccounts())
    monkeypatch.setattr(member_api, "approvals", approvals)
    app = FastAPI()
    app.include_router(member_api.router)
    return TestClient(app), approvals


def _headers():
    return {"Authorization": "Bearer approval-session-token"}


def test_approval_challenge_is_bound_to_authenticated_session_and_does_not_execute(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.post(
        "/api/aura-sec/member/actions/action-1/approval-challenge",
        headers=_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert approvals.challenge_calls == [(USER["id"], "action-1", "approval-session-token")]
    assert data["one_time"] is True
    assert data["command_issued"] is False
    assert data["generic_command_execution_available"] is False
    assert "cannot issue or execute" in data["truth"]


def test_approval_confirm_forwards_password_only_to_reauth_gateway(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.post(
        "/api/aura-sec/member/actions/action-wipe/approve",
        headers=_headers(),
        json={
            "approval_token": "approval-token-1234567890",
            "password": "member-password-value",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert approvals.approve_calls == [
        (
            USER["id"],
            "action-wipe",
            "approval-session-token",
            "approval-token-1234567890",
            "member-password-value",
        )
    ]
    assert data["approved"] is True
    assert data["command_issued"] is False


def test_approval_routes_require_authentication(monkeypatch):
    client, _approvals = _client(monkeypatch)
    assert client.post("/api/aura-sec/member/actions/action-1/approval-challenge").status_code == 401
    assert client.post(
        "/api/aura-sec/member/actions/action-1/approve",
        json={"approval_token": "approval-token-1234567890"},
    ).status_code == 401


def test_approval_payload_rejects_unknown_execution_fields(monkeypatch):
    client, _approvals = _client(monkeypatch)
    response = client.post(
        "/api/aura-sec/member/actions/action-1/approve",
        headers=_headers(),
        json={
            "approval_token": "approval-token-1234567890",
            "shell": "powershell -enc forbidden",
        },
    )
    assert response.status_code == 422
