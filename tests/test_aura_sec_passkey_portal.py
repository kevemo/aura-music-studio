from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_passkey_portal as portal
from aura_music_studio.aura_sec_approval_write_guard import AuraSecApprovalWriteGuardMiddleware


USER = {
    "id": "passkey-portal-user",
    "email": "passkey.portal@example.test",
    "display_name": "Passkey Portal User",
    "status": "active",
    "plan_id": "free",
}
SAME_ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


class FakeAccounts:
    def resolve_session(self, token):
        return dict(USER) if token == "passkey-portal-session" else None


class FakeSecurity:
    def get_action(self, _user_id, action_id):
        if action_id == "wipe-action":
            return {
                "id": action_id,
                "device_id": "device-1",
                "action_type": "remote_wipe",
                "risk_class": "strong_reauth_required",
                "status": "proposed",
                "details": {"summary": "Remote wipe needs passkey step-up."},
            }
        if action_id == "normal-action":
            return {
                "id": action_id,
                "device_id": "device-1",
                "action_type": "isolate_network",
                "risk_class": "confirmation_required",
                "status": "proposed",
                "details": {},
            }
        raise ValueError("not found")


class FakePasskeys:
    def list_credentials(self, _user_id):
        return [
            {
                "id": "credential-record-1",
                "label": "Studio passkey",
                "device_type": "multi_device",
                "backed_up": True,
                "created_at": "2026-08-27T10:00:00+00:00",
                "last_used_at": None,
            }
        ]

    def begin_registration(self, user_id, *, session_token, password, label):
        assert user_id == USER["id"]
        assert session_token == "passkey-portal-session"
        assert password == "current-password"
        return {
            "ceremony_id": "registration-ceremony-123456",
            "public_key": {"challenge": "AA", "user": {"id": "AA"}, "excludeCredentials": []},
            "label": label,
            "private_key_received_by_server": False,
            "biometric_data_received_by_server": False,
        }

    def complete_registration(self, user_id, *, session_token, ceremony_id, credential_response, label):
        return {
            "registered": True,
            "credential": {"id": "credential-record-2", "label": label},
            "private_key_received_by_server": False,
            "biometric_data_received_by_server": False,
        }

    def begin_action_verification(self, user_id, action_id, *, session_token):
        assert action_id == "wipe-action"
        return {
            "ceremony_id": "action-ceremony-123456",
            "action_id": action_id,
            "public_key": {"challenge": "AA", "allowCredentials": [], "userVerification": "required"},
            "user_verification_required": True,
            "command_issued": False,
        }

    def complete_action_verification(
        self,
        user_id,
        action_id,
        *,
        session_token,
        ceremony_id,
        credential_response,
    ):
        return {
            "verified": True,
            "evidence_id": "passkey-evidence-123456789",
            "action_id": action_id,
            "method": "webauthn",
            "user_verified": True,
            "one_time": True,
            "command_issued": False,
        }


class FakeApprovals:
    def __init__(self):
        self.approve_calls = []

    def create_challenge(self, _user_id, action_id, *, session_token):
        strong = action_id == "wipe-action"
        return {
            "challenge_id": f"challenge-{action_id}",
            "approval_token": f"approval-token-{action_id}-123456",
            "action_id": action_id,
            "risk_class": "strong_reauth_required" if strong else "confirmation_required",
            "strong_reauthentication_required": strong,
            "passkey_enrolled": strong,
            "passkey_required": strong,
            "expires_at": "2026-08-27T13:00:00+00:00",
            "one_time": True,
            "command_issued": False,
        }

    def approve(
        self,
        user_id,
        action_id,
        *,
        session_token,
        approval_token,
        password=None,
        strong_reauth_evidence_id=None,
    ):
        self.approve_calls.append(
            (user_id, action_id, session_token, approval_token, password, strong_reauth_evidence_id)
        )
        return {
            "approved": True,
            "action": {"id": action_id, "action_type": "remote_wipe", "status": "approved"},
            "strong_reauthentication_verified": True,
            "strong_reauthentication_method": "webauthn",
            "command_issued": False,
            "truth": "Approval recorded; native command still requires signed device polling.",
        }


def _client(monkeypatch, *, signed_in=True):
    accounts = FakeAccounts()
    if not signed_in:
        accounts.resolve_session = lambda _token: None
    fake_approvals = FakeApprovals()
    monkeypatch.setattr(portal, "accounts", accounts)
    monkeypatch.setattr(portal, "security", FakeSecurity())
    monkeypatch.setattr(portal, "passkeys", FakePasskeys())
    monkeypatch.setattr(portal, "approvals", fake_approvals)
    app = FastAPI()
    app.include_router(portal.router)
    app.add_middleware(AuraSecApprovalWriteGuardMiddleware)
    client = TestClient(app)
    client.cookies.set(portal.MEMBER_COOKIE, "passkey-portal-session")
    return client, fake_approvals


def test_passkey_management_page_discloses_no_private_key_or_biometric_storage(monkeypatch):
    client, _approvals = _client(monkeypatch)
    response = client.get("/aura-sec/passkeys")
    assert response.status_code == 200
    assert "Studio passkey" in response.text
    assert "private key and biometric data stay with your authenticator" in response.text
    assert "Downgrade protection" in response.text
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def test_passkey_registration_options_require_same_origin(monkeypatch):
    client, _approvals = _client(monkeypatch)
    rejected = client.post(
        "/aura-sec/passkeys/register/options",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        json={"password": "current-password", "label": "Studio passkey"},
    )
    assert rejected.status_code == 403
    accepted = client.post(
        "/aura-sec/passkeys/register/options",
        headers=SAME_ORIGIN,
        json={"password": "current-password", "label": "Studio passkey"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["private_key_received_by_server"] is False
    assert accepted.json()["biometric_data_received_by_server"] is False


def test_high_risk_start_uses_passkey_not_password_when_passkey_enrolled(monkeypatch):
    client, _approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/wipe-action/start",
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 200
    assert "Verify with passkey" in response.text
    assert "password downgrade is disabled" in response.text
    assert "type='password'" not in response.text
    assert "passkey-approval-submit" in response.text


def test_normal_confirmation_action_still_uses_one_time_approval_without_passkey(monkeypatch):
    client, _approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/normal-action/start",
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 200
    assert "Approve this bounded action" in response.text
    assert "Verify with passkey" not in response.text
    assert "type='password'" not in response.text


def test_passkey_assertion_endpoints_return_only_action_evidence_not_command(monkeypatch):
    client, _approvals = _client(monkeypatch)
    begin = client.post(
        "/aura-sec/approval/wipe-action/passkey/options",
        headers=SAME_ORIGIN,
        json={},
    )
    assert begin.status_code == 200
    assert begin.json()["user_verification_required"] is True
    assert begin.json()["command_issued"] is False

    complete = client.post(
        "/aura-sec/approval/wipe-action/passkey/complete",
        headers=SAME_ORIGIN,
        json={
            "ceremony_id": "action-ceremony-123456",
            "credential_response": {"id": "credential-public-id"},
        },
    )
    assert complete.status_code == 200
    assert complete.json()["evidence_id"] == "passkey-evidence-123456789"
    assert complete.json()["command_issued"] is False


def test_final_high_risk_confirm_carries_only_evidence_id_and_records_no_command(monkeypatch):
    client, approvals = _client(monkeypatch)
    response = client.post(
        "/aura-sec/approval/wipe-action/confirm",
        headers=SAME_ORIGIN,
        data={
            "approval_token": "approval-token-wipe-action-123456",
            "strong_reauth_evidence_id": "passkey-evidence-123456789",
        },
    )
    assert response.status_code == 200
    assert approvals.approve_calls == [
        (
            USER["id"],
            "wipe-action",
            "passkey-portal-session",
            "approval-token-wipe-action-123456",
            None,
            "passkey-evidence-123456789",
        )
    ]
    assert "Re-authentication: webauthn" in response.text
    assert "No command was issued by this form" in response.text


def test_passkey_page_requires_member_login(monkeypatch):
    client, _approvals = _client(monkeypatch, signed_in=False)
    response = client.get("/aura-sec/passkeys", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin?next=")
