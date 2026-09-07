import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore, PASSWORD_SCHEME_ARGON2ID
from aura_music_studio import account_security_api as security_api
from aura_music_studio.account_security_api import AccountSecurityService


def _active_user(store: AccountStore, email: str = "member@example.com"):
    signup = store.signup(email, "Member", "original-password", "free")
    with store._connect() as con:
        con.execute("UPDATE users SET status='active' WHERE id=?", (signup.user_id,))
    return signup.user_id


def test_password_reset_is_single_use_and_revokes_every_existing_session(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store)
    first = store.create_session(user_id)
    second = store.create_session(user_id)
    service = AccountSecurityService(store)

    issued = service.create_password_reset("member@example.com")
    assert issued["issued"] is True
    raw_token = issued["token"]

    result = service.complete_password_reset(raw_token, "replacement-password")
    assert result == {"reset": True, "user_id": user_id, "sessions_revoked": True}
    assert store.resolve_session(first) is None
    assert store.resolve_session(second) is None

    user = store.get_user(user_id)
    assert user is not None
    assert user["password_scheme"] == PASSWORD_SCHEME_ARGON2ID
    assert user["password_salt"] == ""
    assert store.authenticate("member@example.com", "replacement-password") is not None
    assert store.authenticate("member@example.com", "original-password") is None

    with pytest.raises(ValueError, match="invalid or expired"):
        service.complete_password_reset(raw_token, "another-password")


def test_new_reset_invalidates_previous_link_and_request_throttle_is_hashed(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    _active_user(store)
    service = AccountSecurityService(store)

    first = service.create_password_reset("member@example.com")
    second = service.create_password_reset("member@example.com")
    third = service.create_password_reset("member@example.com")
    fourth = service.create_password_reset("member@example.com")

    assert first["issued"] and second["issued"] and third["issued"]
    assert fourth == {"issued": False, "reason": "generic"}
    with pytest.raises(ValueError, match="invalid or expired"):
        service.complete_password_reset(first["token"], "replacement-password")

    with sqlite3.connect(store.db_path) as con:
        con.row_factory = sqlite3.Row
        throttle = con.execute("SELECT * FROM password_reset_throttle").fetchone()
    assert throttle is not None
    assert "member@example.com" not in " ".join(str(value) for value in throttle)
    assert len(str(throttle["identity_hash"])) == 64


def test_unknown_reset_requests_are_generic_and_rate_limited_without_user_rows(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    service = AccountSecurityService(store)

    for _ in range(4):
        assert service.create_password_reset("missing@example.com") == {"issued": False, "reason": "generic"}

    with sqlite3.connect(store.db_path) as con:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        throttles = con.execute("SELECT COUNT(*) FROM password_reset_throttle").fetchone()[0]
        reset_tokens = con.execute("SELECT COUNT(*) FROM password_reset_tokens").fetchone()[0]
    assert users == 0
    assert throttles == 1
    assert reset_tokens == 0


def test_session_listing_never_exposes_token_hashes_and_revoke_others_preserves_current(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store)
    current = store.create_session(user_id)
    other_one = store.create_session(user_id)
    other_two = store.create_session(user_id)
    service = AccountSecurityService(store)

    items = service.list_sessions(current)
    assert len(items) == 3
    assert sum(1 for item in items if item["current"]) == 1
    assert all("token_hash" not in item for item in items)
    assert all(current not in str(item) for item in items)
    assert all(other_one not in str(item) for item in items)

    assert service.revoke_other_sessions(current) == 2
    assert store.resolve_session(current) is not None
    assert store.resolve_session(other_one) is None
    assert store.resolve_session(other_two) is None


def test_member_cannot_revoke_another_users_session(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    first_user = _active_user(store, "first@example.com")
    second_user = _active_user(store, "second@example.com")
    first_token = store.create_session(first_user)
    second_token = store.create_session(second_user)
    service = AccountSecurityService(store)

    second_session_id = next(item["id"] for item in service.list_sessions(second_token) if item["current"])
    with pytest.raises(ValueError, match="Session not found"):
        service.revoke_session_id(first_token, second_session_id)
    assert store.resolve_session(second_token) is not None


def test_reset_request_route_is_non_enumerating_and_does_not_return_reset_token(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    _active_user(store)
    service = AccountSecurityService(store)
    monkeypatch.setattr(security_api, "service", service)
    delivered = []
    monkeypatch.setattr(security_api, "send_email", lambda *args, **kwargs: delivered.append((args, kwargs)) or {"sent": True})
    monkeypatch.setattr(security_api, "_public_url", lambda: "https://pulsar.example")

    app = FastAPI()
    app.include_router(security_api.router)
    client = TestClient(app)

    known = client.post("/auth/password-reset/request", json={"email": "member@example.com"})
    unknown = client.post("/auth/password-reset/request", json={"email": "missing@example.com"})
    assert known.status_code == 200
    assert unknown.status_code == 200
    assert known.json() == unknown.json()
    assert known.json()["accepted"] is True
    assert "token" not in known.json()
    assert len(delivered) == 1
    assert "https://pulsar.example/auth/reset-password#token=" in delivered[0][0][2]


def test_session_routes_require_authentication_and_current_revocation_clears_cookie(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _active_user(store)
    token = store.create_session(user_id)
    service = AccountSecurityService(store)
    monkeypatch.setattr(security_api, "service", service)

    app = FastAPI()
    app.include_router(security_api.router)
    client = TestClient(app)

    assert client.get("/auth/sessions").status_code == 401
    client.cookies.set("lss_session", token)
    listing = client.get("/auth/sessions")
    assert listing.status_code == 200
    assert listing.json()["raw_tokens_exposed"] is False
    current_id = next(item["id"] for item in listing.json()["sessions"] if item["current"])

    revoked = client.delete(f"/auth/sessions/{current_id}")
    assert revoked.status_code == 200
    assert revoked.json()["current"] is True
    assert store.resolve_session(token) is None


def test_shared_security_composition_matches_account_security_routes():
    from aura_music_studio.api import app as base_app
    from aura_music_studio import security

    client = TestClient(base_app)
    assert client.get("/auth/forgot-password").status_code == 200
    assert client.get("/auth/reset-password").status_code == 200
    # Private account-security endpoints must be matched and reject unauthenticated access,
    # not disappear as 404s due to router composition.
    assert client.get("/auth/sessions").status_code == 401
    reset = client.post(
        "/auth/password-reset/request",
        json={"email": "route-check@example.invalid"},
    )
    assert reset.status_code == 200
    invalid_confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": "synthetic-route-check-value", "new_password": "new-route-password"},
    )
    assert invalid_confirm.status_code == 400
    assert "/auth/password-reset/request" in security.AUTH_RATE_PATHS
    assert "/auth/password-reset/confirm" in security.AUTH_RATE_PATHS
