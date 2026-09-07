import sqlite3

import pytest
from fastapi import APIRouter, FastAPI, Form, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from aura_music_studio.accounts import AccountStore
from aura_music_studio.email_verification import EmailVerificationService
from aura_music_studio import email_verification as verification_module
from aura_music_studio import email_verification_integration as integration_module
from aura_music_studio.email_verification_integration import install_email_verification


class SignupPayload(BaseModel):
    email: str
    display_name: str
    password: str
    plan_id: str = "free"


def test_rollout_grandfathers_existing_accounts_but_not_new_accounts(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    legacy = store.signup("legacy@example.com", "Legacy", "legacy-password", "free")

    service = EmailVerificationService(store)
    assert service.is_verified(legacy.user_id) is True

    new_user = store.signup("new@example.com", "New User", "new-user-password", "free")
    service.register_new_user(new_user.user_id)
    assert service.is_verified(new_user.user_id) is False


def test_verification_token_is_single_use_and_new_issue_invalidates_old_link(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    service = EmailVerificationService(store)
    signup = store.signup("member@example.com", "Member", "member-password", "free")
    service.register_new_user(signup.user_id)

    first = service.issue_for_user(signup.user_id)
    second = service.issue_for_user(signup.user_id)
    assert first["issued"] and second["issued"]

    with pytest.raises(ValueError, match="invalid or expired"):
        service.complete(first["token"])

    assert service.complete(second["token"]) == {"verified": True, "user_id": signup.user_id}
    assert service.is_verified(signup.user_id) is True

    with pytest.raises(ValueError, match="invalid or expired"):
        service.complete(second["token"])


def test_verification_request_throttle_hashes_email_and_unknown_requests_are_generic(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    service = EmailVerificationService(store)

    for _ in range(4):
        result = service.request_for_email("missing@example.com")
        assert result.get("issued") is False

    with sqlite3.connect(store.db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM email_verification_throttle").fetchone()
        tokens = con.execute("SELECT COUNT(*) FROM email_verification_tokens").fetchone()[0]
    assert row is not None
    assert "missing@example.com" not in " ".join(str(value) for value in row)
    assert len(str(row["identity_hash"])) == 64
    assert tokens == 0


def _membership_test_app(store: AccountStore, service: EmailVerificationService) -> FastAPI:
    router = APIRouter()

    @router.post("/auth/signup")
    def signup(payload: SignupPayload):
        result = store.signup(payload.email, payload.display_name, payload.password, payload.plan_id)
        return {
            "created": True,
            "status": "pending_approval",
            "requested_plan": result.requested_plan,
        }

    @router.post("/membership/decision")
    def decision(
        token: str = Form(...),
        decision: str = Form(...),
        decided_by: str = Form(...),
    ):
        before = store.membership_request_from_token(token)
        if not before:
            raise HTTPException(404, "Membership request not found")
        store.decide_membership(token, decision, decided_by)
        return {"approved": decision.lower() == "approve"}

    install_email_verification(router, service)
    app = FastAPI()
    app.include_router(router)
    return app


def test_signup_wrapper_enrols_new_member_and_approval_fails_until_verified(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    service = EmailVerificationService(store)
    delivered = []
    monkeypatch.setattr(
        integration_module,
        "deliver_email_verification",
        lambda email, name, token: delivered.append((email, name, token)) or {"sent": True},
    )
    client = TestClient(_membership_test_app(store, service))

    signup_response = client.post(
        "/auth/signup",
        json={
            "email": "verify@example.com",
            "display_name": "Verify Me",
            "password": "verification-password",
            "plan_id": "free",
        },
    )
    assert signup_response.status_code == 200
    body = signup_response.json()
    assert body["email_verification_required"] is True
    assert body["email_verified"] is False
    assert "token" not in body
    assert len(delivered) == 1

    user = store.get_user_by_email("verify@example.com")
    assert user is not None
    assert service.is_verified(user["id"]) is False
    with store._connect() as con:
        request_row = con.execute(
            "SELECT * FROM membership_requests WHERE user_id=?",
            (user["id"],),
        ).fetchone()
        token_row = con.execute(
            "SELECT * FROM email_verification_tokens WHERE user_id=? AND used_at IS NULL",
            (user["id"],),
        ).fetchone()
    assert request_row is not None
    assert token_row is not None

    # The approval token is intentionally not exposed by the public signup response. Use the
    # private test fixture result by creating a second controlled request for approval testing.
    controlled = store.signup("controlled@example.com", "Controlled", "controlled-password", "free")
    service.register_new_user(controlled.user_id)
    blocked = client.post(
        "/membership/decision",
        data={"token": controlled.approval_token, "decision": "approve", "decided_by": "Kev"},
    )
    assert blocked.status_code == 409
    assert "verify" in blocked.json()["detail"].lower()

    issued = service.issue_for_user(controlled.user_id)
    service.complete(issued["token"])
    approved = client.post(
        "/membership/decision",
        data={"token": controlled.approval_token, "decision": "approve", "decided_by": "Kev"},
    )
    assert approved.status_code == 200
    assert store.get_user(controlled.user_id)["status"] == "active"


def test_verification_email_uses_fragment_and_page_never_reads_query_string(monkeypatch):
    delivered = []
    monkeypatch.setattr(verification_module, "_public_url", lambda: "https://pulsar.example")
    monkeypatch.setattr(
        verification_module,
        "send_email",
        lambda to, subject, body: delivered.append((to, subject, body)) or {"sent": True},
    )

    result = verification_module.deliver_email_verification(
        "member@example.com",
        "Member",
        "secret-verification-token",
    )
    assert result["sent"] is True
    assert len(delivered) == 1
    body = delivered[0][2]
    assert "https://pulsar.example/auth/verify-email#token=" in body
    assert "/auth/verify-email?token=" not in body

    page = verification_module.verify_email_page()
    text = page.body.decode("utf-8")
    assert "window.location.hash" in text
    assert "window.location.search" not in text
    assert "history.replaceState({},'', '/auth/verify-email')" in text


def test_security_composition_contains_verification_routes_and_rate_limits():
    from aura_music_studio.api import app
    from aura_music_studio import security

    client = TestClient(app)
    page = client.get("/auth/verify-email")
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    assert "serviceWorker.register" not in page.text

    request = client.post(
        "/auth/email-verification/request",
        json={"email": "route-check@example.invalid"},
    )
    assert request.status_code == 200
    assert request.json()["accepted"] is True
    assert "/auth/email-verification/request" in security.AUTH_RATE_PATHS
    assert "/auth/email-verification/confirm" in security.AUTH_RATE_PATHS
