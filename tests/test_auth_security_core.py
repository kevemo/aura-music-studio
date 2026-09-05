import sqlite3
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from aura_music_studio.accounts import (
    AccountStore,
    PASSWORD_SCHEME_ARGON2ID,
    PASSWORD_SCHEME_PBKDF2,
    _hash_password,
)
from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware


def test_new_accounts_use_argon2id_and_authenticate(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("new@example.com", "New Member", "a-secure-password", "free")
    user = store.get_user(signup.user_id)
    assert user is not None
    assert user["password_scheme"] == PASSWORD_SCHEME_ARGON2ID
    assert user["password_salt"] == ""
    assert str(user["password_hash"]).startswith("$argon2id$")
    assert user["password_updated_at"]

    authenticated = store.authenticate("new@example.com", "a-secure-password")
    assert authenticated is not None
    assert authenticated["id"] == signup.user_id


def test_existing_pbkdf2_database_is_migrated_and_rehashed_after_successful_login(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    salt, digest = _hash_password("legacy-password")
    with sqlite3.connect(db_path) as con:
        con.execute(
            """CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_approval',
                plan_id TEXT NOT NULL DEFAULT 'free',
                requested_plan_id TEXT NOT NULL DEFAULT 'free',
                billing_status TEXT NOT NULL DEFAULT 'not_required',
                created_at TEXT NOT NULL,
                approved_at TEXT,
                approved_by TEXT,
                rejected_at TEXT,
                rejected_by TEXT,
                disabled_at TEXT
            )"""
        )
        con.execute(
            """INSERT INTO users
               (id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at)
               VALUES ('legacy-user','legacy@example.com','Legacy',?,?,'active','free','free','not_required','2026-01-01T00:00:00+00:00')""",
            (salt, digest),
        )

    store = AccountStore(db_path)
    before = store.get_user("legacy-user")
    assert before is not None
    assert before["password_scheme"] == PASSWORD_SCHEME_PBKDF2

    authenticated = store.authenticate("legacy@example.com", "legacy-password")
    assert authenticated is not None
    after = store.get_user("legacy-user")
    assert after is not None
    assert after["password_scheme"] == PASSWORD_SCHEME_ARGON2ID
    assert after["password_salt"] == ""
    assert str(after["password_hash"]).startswith("$argon2id$")
    assert after["password_hash"] != digest


def test_login_failures_trigger_shared_account_throttle(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    store.signup("throttle@example.com", "Throttle", "correct-password", "free")

    for _ in range(5):
        assert store.authenticate("throttle@example.com", "wrong-password") is None

    status = store.login_throttle_status("THROTTLE@example.com")
    assert status["blocked"] is True
    assert status["failure_count"] == 5
    assert status["retry_after_seconds"] > 0
    # Correct credentials cannot bypass the active throttle by choosing another login surface.
    assert store.authenticate("throttle@example.com", "correct-password") is None

    store.clear_login_throttle("throttle@example.com")
    assert store.authenticate("throttle@example.com", "correct-password") is not None
    assert store.login_throttle_status("throttle@example.com")["failure_count"] == 0


def _guarded_app() -> FastAPI:
    app = FastAPI()

    @app.post("/mutate")
    def mutate(response: Response):
        response.headers["Vary"] = "Accept-Encoding"
        return {"ok": True}

    app.add_middleware(CrossSiteRequestGuardMiddleware)
    return app


def test_cross_site_guard_blocks_fetch_metadata_and_origin_mismatch():
    client = TestClient(_guarded_app())

    ordinary = client.post("/mutate")
    assert ordinary.status_code == 200
    vary = {part.strip().lower() for part in ordinary.headers["vary"].split(",")}
    assert {"accept-encoding", "sec-fetch-site", "origin"}.issubset(vary)

    assert client.post(
        "/mutate",
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
    ).status_code == 200

    cross = client.post("/mutate", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    assert cross.json()["security_gate"] == "fetch_metadata"

    sibling = client.post("/mutate", headers={"Sec-Fetch-Site": "same-site"})
    assert sibling.status_code == 403

    origin = client.post("/mutate", headers={"Origin": "https://attacker.invalid"})
    assert origin.status_code == 403
    assert origin.json()["security_gate"] == "origin"

    malformed = client.post("/mutate", headers={"Origin": "https://example.invalid:bad-port"})
    assert malformed.status_code == 403
    assert malformed.json()["security_gate"] == "origin"

    referer = client.post("/mutate", headers={"Referer": "https://attacker.invalid/form"})
    assert referer.status_code == 403
    assert referer.json()["security_gate"] == "referer"


def test_bearer_api_requests_do_not_depend_on_browser_csrf_headers():
    client = TestClient(_guarded_app())
    response = client.post(
        "/mutate",
        headers={
            "Authorization": "Bearer opaque-api-token",
            "Origin": "https://attacker.invalid",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert response.status_code == 200


def test_production_app_registers_cross_site_guard():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware" in source
    assert "app.add_middleware(CrossSiteRequestGuardMiddleware)" in source
