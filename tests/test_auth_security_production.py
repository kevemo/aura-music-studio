from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(CrossSiteRequestGuardMiddleware)

    @app.post("/write")
    def write():
        return {"ok": True}

    return app


def test_production_does_not_reflect_incoming_host_into_allowed_origins(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")
    monkeypatch.delenv("AURA_ALLOWED_ORIGINS", raising=False)

    with TestClient(_app(), base_url="https://evil.example") as client:
        response = client.post(
            "/write",
            headers={"Origin": "https://evil.example"},
            cookies={"lss_session": "ambient-cookie"},
        )

    assert response.status_code == 403
    assert response.json()["security_gate"] == "origin"


def test_production_accepts_explicitly_configured_origin_even_if_host_differs(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")
    monkeypatch.delenv("AURA_ALLOWED_ORIGINS", raising=False)

    with TestClient(_app(), base_url="https://untrusted-host.example") as client:
        response = client.post(
            "/write",
            headers={"Origin": "https://command.example"},
            cookies={"lss_session": "ambient-cookie"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_production_cookie_write_without_browser_origin_evidence_fails_closed(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")

    with TestClient(_app(), base_url="https://command.example") as client:
        response = client.post(
            "/write",
            cookies={"lss_session": "ambient-cookie"},
        )

    assert response.status_code == 403
    assert response.json()["security_gate"] == "browser_origin_evidence"


def test_production_bearer_client_does_not_need_browser_origin_protocol(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")

    with TestClient(_app(), base_url="https://api-client.invalid") as client:
        response = client.post(
            "/write",
            headers={"Authorization": "Bearer explicit-api-token"},
        )

    assert response.status_code == 200


def test_development_keeps_request_origin_fallback(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "development")
    monkeypatch.delenv("LSS_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("AURA_ALLOWED_ORIGINS", raising=False)

    with TestClient(_app(), base_url="http://localhost:8000") as client:
        response = client.post(
            "/write",
            headers={"Origin": "http://localhost:8000"},
            cookies={"lss_session": "ambient-cookie"},
        )

    assert response.status_code == 200


def test_configured_origins_reject_credentials_paths_queries_and_fragments(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv(
        "AURA_ALLOWED_ORIGINS",
        "https://user:pass@example.com,https://example.com/path,https://example.net?q=1,https://example.org#x",
    )
    monkeypatch.delenv("LSS_PUBLIC_BASE_URL", raising=False)

    with TestClient(_app(), base_url="https://example.com") as client:
        response = client.post(
            "/write",
            headers={"Origin": "https://example.com"},
            cookies={"lss_session": "ambient-cookie"},
        )

    assert response.status_code == 403
    assert response.json()["security_gate"] == "origin"
