from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.account_security_api import AccountSecurityService
from aura_music_studio import account_security_api, csrf_tokens, security
from aura_music_studio.csrf_tokens import CSRF_HEADER, CSRF_TTL_SECONDS, SessionCsrfService
from aura_music_studio.security import StudioSecurityMiddleware


def _active_user(store: AccountStore, email: str) -> str:
    signup = store.signup(email, "Member", "strong-member-password", "free")
    with store._connect() as con:
        con.execute("UPDATE users SET status='active' WHERE id=?", (signup.user_id,))
    return signup.user_id


def test_csrf_token_is_bound_to_one_live_session_and_expires(tmp_path, monkeypatch):
    monkeypatch.delenv("LSS_CSRF_HMAC_KEY", raising=False)
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store, "member@example.com")
    first_session = store.create_session(user_id)
    second_session = store.create_session(user_id)
    service = SessionCsrfService(store)

    issued = service.issue(first_session, now=1_000)
    token = issued["token"]
    assert first_session not in token
    assert issued["header"] == CSRF_HEADER
    assert service.verify(first_session, token, now=1_001) is True
    assert service.verify(second_session, token, now=1_001) is False
    assert service.verify(first_session, token + "tampered", now=1_001) is False
    assert service.verify(first_session, token, now=1_000 + CSRF_TTL_SECONDS + 1) is False

    store.revoke_session(first_session)
    assert service.verify(first_session, token, now=1_002) is False


def test_database_signing_key_is_stable_and_not_a_session_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("LSS_CSRF_HMAC_KEY", raising=False)
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store, "member@example.com")
    session = store.create_session(user_id)

    first = SessionCsrfService(store)
    token = first.issue(session, now=2_000)["token"]
    second = SessionCsrfService(store)
    assert second.verify(session, token, now=2_001) is True

    with store._connect() as con:
        row = con.execute("SELECT value FROM csrf_security_meta WHERE key='hmac_key'").fetchone()
    assert row is not None
    persisted = str(row["value"])
    assert len(persisted) == 64
    assert session not in persisted
    assert session not in token


def _middleware_app() -> FastAPI:
    app = FastAPI()

    @app.post("/safe-write")
    def safe_write():
        return {"ok": True}

    @app.delete("/privacy/account")
    def delete_account_probe():
        return {"deleted": True}

    @app.delete("/auth/sessions/{session_id}")
    def revoke_session_probe(session_id: str):
        return {"revoked": session_id}

    @app.post("/auth/sessions/revoke-others")
    def revoke_other_sessions_probe():
        return {"revoked": "others"}

    app.add_middleware(StudioSecurityMiddleware)
    return app


def test_middleware_requires_csrf_only_for_destructive_cookie_actions(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store, "member@example.com")
    session = store.create_session(user_id)
    csrf = SessionCsrfService(store)
    monkeypatch.setattr(security, "csrf_service", csrf)

    client = TestClient(_middleware_app())
    client.cookies.set("lss_session", session)

    # Existing non-destructive writes keep their current behavior.
    assert client.post("/safe-write").status_code == 200

    for method, path in (
        ("DELETE", "/privacy/account"),
        ("DELETE", "/auth/sessions/session-id"),
        ("POST", "/auth/sessions/revoke-others"),
    ):
        blocked = client.request(method, path)
        assert blocked.status_code == 403
        assert blocked.json()["security_gate"] == "session_csrf"
        assert blocked.json()["csrf_header"] == CSRF_HEADER

    token = csrf.issue(session)["token"]
    headers = {CSRF_HEADER: token, "Origin": "http://testserver"}
    assert client.delete("/privacy/account", headers=headers).status_code == 200
    assert client.delete("/auth/sessions/session-id", headers=headers).status_code == 200
    assert client.post("/auth/sessions/revoke-others", headers=headers).status_code == 200


def test_cross_site_request_stays_blocked_even_with_valid_csrf_token(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store, "member@example.com")
    session = store.create_session(user_id)
    csrf = SessionCsrfService(store)
    monkeypatch.setattr(security, "csrf_service", csrf)
    token = csrf.issue(session)["token"]

    client = TestClient(_middleware_app())
    client.cookies.set("lss_session", session)
    response = client.delete(
        "/privacy/account",
        headers={CSRF_HEADER: token, "Origin": "https://attacker.invalid"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site write request blocked"


def test_bearer_client_is_not_forced_into_browser_csrf_protocol():
    client = TestClient(_middleware_app())
    response = client.delete(
        "/privacy/account",
        headers={
            "Authorization": "Bearer opaque-api-session",
            "Origin": "https://api-client.invalid",
        },
    )
    assert response.status_code == 200


def test_real_session_routes_use_issued_csrf_token(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store, "member@example.com")
    current_session = store.create_session(user_id)
    other_session = store.create_session(user_id)
    account_service = AccountSecurityService(store)
    csrf_service = SessionCsrfService(store)

    monkeypatch.setattr(account_security_api, "service", account_service)
    monkeypatch.setattr(csrf_tokens, "service", csrf_service)
    monkeypatch.setattr(security, "csrf_service", csrf_service)

    app = FastAPI()
    app.include_router(account_security_api.router)
    app.include_router(csrf_tokens.router)
    app.add_middleware(StudioSecurityMiddleware)
    client = TestClient(app)
    client.cookies.set("lss_session", current_session)

    listing = client.get("/auth/sessions")
    assert listing.status_code == 200
    other_id = next(
        item["id"] for item in listing.json()["sessions"] if not item["current"] and item["active"]
    )

    assert client.delete(f"/auth/sessions/{other_id}").status_code == 403
    issued = client.get("/auth/csrf-token")
    assert issued.status_code == 200
    csrf_value = issued.json()["csrf_token"]
    assert current_session not in csrf_value
    assert issued.json()["header"] == CSRF_HEADER

    revoked = client.delete(
        f"/auth/sessions/{other_id}",
        headers={CSRF_HEADER: csrf_value, "Origin": "http://testserver"},
    )
    assert revoked.status_code == 200
    assert store.resolve_session(other_session) is None
    assert store.resolve_session(current_session) is not None


def test_production_composition_exposes_csrf_token_route_and_requires_cookie():
    from aura_music_studio.api import app as base_app

    client = TestClient(base_app)
    response = client.get("/auth/csrf-token")
    assert response.status_code == 401
    assert response.headers.get("cache-control") == "no-store"
