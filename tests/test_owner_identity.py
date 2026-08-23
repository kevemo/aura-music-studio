from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.audit import AuditLedger
import aura_music_studio.owner_identity as owner_identity


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-test-secret")
    monkeypatch.setenv("LSS_COOKIE_SECURE", "false")
    monkeypatch.setenv("LSS_OWNER_ACTOR_TTL_HOURS", "12")
    store = AccountStore(tmp_path / "accounts.sqlite3")
    audit = AuditLedger(store)
    monkeypatch.setattr(owner_identity, "AUDIT", audit)

    app = FastAPI()
    app.add_middleware(owner_identity.OwnerIdentityMiddleware)
    app.include_router(owner_identity.router)

    @app.get("/owner/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request):
        actor = getattr(request.state, "owner_actor", None)
        return HTMLResponse(f"<html><body>Owner dashboard:{actor['display_name'] if actor else 'none'}</body></html>")

    @app.post("/owner/sensitive", response_class=HTMLResponse)
    def sensitive(request: Request):
        actor = getattr(request.state, "owner_actor", None)
        return HTMLResponse(f"<html><body>Changed by {actor['display_name'] if actor else 'none'}</body></html>")

    return app, audit


def test_actor_token_is_signed_and_tamper_evident(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-test-secret")
    token = owner_identity.issue_actor_token("kev", issued_at=10_000)
    assert owner_identity.verify_actor_token(token, now=10_001)["id"] == "kev"

    replacement = "0" if token[-1] != "0" else "1"
    assert owner_identity.verify_actor_token(token[:-1] + replacement, now=10_001) is None


def test_actor_token_expires_with_owner_session_window(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-test-secret")
    monkeypatch.setenv("LSS_OWNER_ACTOR_TTL_HOURS", "1")
    token = owner_identity.issue_actor_token("mary", issued_at=10_000)
    assert owner_identity.verify_actor_token(token, now=13_599)["id"] == "mary"
    assert owner_identity.verify_actor_token(token, now=13_601) is None


def test_invalid_actor_never_gets_token(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-test-secret")
    try:
        owner_identity.issue_actor_token("admin")
        raise AssertionError("invalid actor should have failed")
    except ValueError as exc:
        assert "valid ESP owner profile" in str(exc)


def test_owner_dashboard_requires_profile_after_admin_auth(tmp_path, monkeypatch):
    app, _audit = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    client.cookies.set(owner_identity.ADMIN_COOKIE, "owner-test-secret")

    response = client.get("/owner/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/owner/profile?next=")

    profile = client.get("/owner/profile")
    assert profile.status_code == 200
    assert "Who is using ESP?" in profile.text
    assert "Kev" in profile.text
    assert "Mary" in profile.text


def test_select_kevin_sets_signed_actor_and_audits_actions(tmp_path, monkeypatch):
    app, audit = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    client.cookies.set(owner_identity.ADMIN_COOKIE, "owner-test-secret")

    response = client.post(
        "/owner/profile",
        data={"actor_id": "kev", "next_path": "/owner/dashboard"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/owner/dashboard"
    assert client.cookies.get(owner_identity.OWNER_ACTOR_COOKIE)

    dashboard = client.get("/owner/dashboard")
    assert dashboard.status_code == 200
    assert "Owner dashboard:Kev" in dashboard.text
    assert "ESP Owner · Kev" in dashboard.text

    changed = client.post("/owner/sensitive")
    assert changed.status_code == 200
    assert "Changed by Kev" in changed.text

    recent = audit.recent(10)
    assert any(item["actor"] == "Kev — ESP Co-Owner" and item["action"] == "owner_profile_selected" for item in recent)
    assert any(item["actor"] == "Kev — ESP Co-Owner" and item["action"] == "owner_write" for item in recent)


def test_switching_to_mary_changes_actor_not_permissions(tmp_path, monkeypatch):
    app, audit = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    client.cookies.set(owner_identity.ADMIN_COOKIE, "owner-test-secret")

    client.post("/owner/profile", data={"actor_id": "kev", "next_path": "/owner/dashboard"}, follow_redirects=False)
    client.post("/owner/profile", data={"actor_id": "mary", "next_path": "/owner/dashboard"}, follow_redirects=False)

    dashboard = client.get("/owner/dashboard")
    assert "Owner dashboard:Mary" in dashboard.text
    assert "ESP Owner · Mary" in dashboard.text
    selections = [item for item in audit.recent(10) if item["action"] == "owner_profile_selected"]
    assert selections[0]["actor"] == "Mary — ESP Co-Owner"
    assert selections[0]["details"]["previous_actor_id"] == "kev"


def test_actor_cookie_is_not_owner_authorization(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-test-secret")
    token = owner_identity.issue_actor_token("kev")

    class DummyRequest:
        cookies = {owner_identity.OWNER_ACTOR_COOKIE: token}

    assert owner_identity.admin_authorized(DummyRequest()) is False
    assert owner_identity.actor_from_request(DummyRequest()) is None


def test_next_path_cannot_escape_owner_area():
    assert owner_identity._safe_next("https://evil.example/") == "/owner/dashboard"
    assert owner_identity._safe_next("//evil.example/") == "/owner/dashboard"
    assert owner_identity._safe_next("/dashboard") == "/owner/dashboard"
    assert owner_identity._safe_next("/owner/backups") == "/owner/backups"
