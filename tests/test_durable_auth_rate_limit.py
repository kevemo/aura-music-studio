from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.auth_rate_limit import AuthRateLimitStore
from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CrossSiteRequestGuardMiddleware)

    @app.post("/auth/login")
    def login():
        return {"ok": True}

    return app


def test_store_is_shared_and_hashes_client_identity(tmp_path: Path):
    db = tmp_path / "rate.sqlite3"
    first = AuthRateLimitStore(db)
    second = AuthRateLimitStore(db)
    assert first.allow("203.0.113.41", "/auth/login", limit=2, window_seconds=60, now=1000.0) == (True, 0)
    assert second.allow("203.0.113.41", "/auth/login", limit=2, window_seconds=60, now=1001.0) == (True, 0)
    allowed, retry = first.allow("203.0.113.41", "/auth/login", limit=2, window_seconds=60, now=1002.0)
    assert allowed is False and retry == 58
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT client_sha256, scope FROM auth_rate_events").fetchall()
    finally:
        con.close()
    assert rows and all(row[0] != "203.0.113.41" for row in rows)


def test_sliding_window_reopens_admission(tmp_path: Path):
    store = AuthRateLimitStore(tmp_path / "rate.sqlite3")
    assert store.allow("client", "login", limit=1, window_seconds=10, now=100.0) == (True, 0)
    assert store.allow("client", "login", limit=1, window_seconds=10, now=105.0) == (False, 5)
    assert store.allow("client", "login", limit=1, window_seconds=10, now=111.0) == (True, 0)


def test_concurrent_admission_cannot_exceed_limit(tmp_path: Path):
    db = tmp_path / "rate.sqlite3"
    AuthRateLimitStore(db).allow("setup", "setup", limit=1, window_seconds=1, now=0.0)

    def attempt(_index: int) -> bool:
        allowed, _ = AuthRateLimitStore(db).allow("same-client", "/auth/login", limit=3, window_seconds=60, now=2000.0)
        return allowed

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(results) == 3


def test_production_attempt_budget_is_shared_between_app_instances(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "shared.sqlite3"))
    monkeypatch.setenv("LSS_AUTH_RATE_LIMIT", "2")
    monkeypatch.setenv("LSS_AUTH_RATE_WINDOW_SECONDS", "60")
    with TestClient(_app()) as first, TestClient(_app()) as second:
        assert first.post("/auth/login").status_code == 200
        assert second.post("/auth/login").status_code == 200
        blocked = first.post("/auth/login")
    assert blocked.status_code == 429
    assert blocked.json()["security_gate"] == "durable_auth_rate_limit"
    assert int(blocked.headers["retry-after"]) >= 1


def test_bearer_header_does_not_bypass_auth_attempt_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "shared.sqlite3"))
    monkeypatch.setenv("LSS_AUTH_RATE_LIMIT", "1")
    monkeypatch.setenv("LSS_AUTH_RATE_WINDOW_SECONDS", "60")
    with TestClient(_app()) as client:
        assert client.post("/auth/login", headers={"Authorization": "Bearer arbitrary"}).status_code == 200
        blocked = client.post("/auth/login", headers={"Authorization": "Bearer arbitrary"})
    assert blocked.status_code == 429


def test_production_rate_store_failure_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "missing-parent" / "db.sqlite3"))
    with TestClient(_app()) as client:
        response = client.post("/auth/login")
    assert response.status_code == 503
    assert response.json()["security_gate"] == "durable_auth_rate_limit"
