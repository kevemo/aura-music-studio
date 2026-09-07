from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(CrossSiteRequestGuardMiddleware)

    @app.post("/write")
    def write():
        return {"ok": True}

    @app.get("/identity")
    def identity(request: Request):
        return {
            "client": request.client.host if request.client else None,
            "scheme": request.url.scheme,
            "proxy_auth_visible": "x-esp-proxy-auth" in request.headers,
        }

    return app


def test_production_does_not_reflect_incoming_host_into_allowed_origins(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")
    monkeypatch.delenv("AURA_ALLOWED_ORIGINS", raising=False)
    with TestClient(_app(), base_url="https://evil.example") as client:
        response = client.post("/write", headers={"Origin": "https://evil.example"}, cookies={"lss_session": "ambient"})
    assert response.status_code == 403
    assert response.json()["security_gate"] == "origin"


def test_production_accepts_explicitly_configured_origin(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")
    with TestClient(_app(), base_url="https://untrusted-host.example") as client:
        response = client.post("/write", headers={"Origin": "https://command.example"}, cookies={"lss_session": "ambient"})
    assert response.status_code == 200


def test_production_cookie_write_without_origin_evidence_fails_closed(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")
    with TestClient(_app(), base_url="https://command.example") as client:
        response = client.post("/write", cookies={"lss_session": "ambient"})
    assert response.status_code == 403
    assert response.json()["security_gate"] == "browser_origin_evidence"


def test_production_forwarded_identity_requires_proxy_secret(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.delenv("LSS_TRUSTED_PROXY_TOKEN", raising=False)
    with TestClient(_app()) as client:
        response = client.get("/identity", headers={"X-Forwarded-For": "198.51.100.44", "X-Forwarded-Proto": "https"})
    assert response.status_code == 503
    assert response.json()["security_gate"] == "trusted_proxy_configuration"


def test_production_rejects_spoofed_forwarded_identity(monkeypatch):
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_TRUSTED_PROXY_TOKEN", "proxy-boundary-secret-0123456789abcdef")
    with TestClient(_app()) as client:
        response = client.get(
            "/identity",
            headers={
                "X-Forwarded-For": "198.51.100.44",
                "X-Forwarded-Proto": "https",
                "X-ESP-Proxy-Auth": "wrong-but-long-enough-secret-value",
            },
        )
    assert response.status_code == 403
    assert response.json()["security_gate"] == "trusted_proxy_authentication"


def test_authenticated_proxy_sets_identity_and_strips_internal_secret(monkeypatch):
    token = "proxy-boundary-secret-0123456789abcdef"
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_TRUSTED_PROXY_TOKEN", token)
    with TestClient(_app(), base_url="http://internal-app") as client:
        response = client.get(
            "/identity",
            headers={"X-Forwarded-For": "2001:db8::42", "X-Forwarded-Proto": "https", "X-ESP-Proxy-Auth": token},
        )
    assert response.status_code == 200
    assert response.json() == {"client": "2001:db8::42", "scheme": "https", "proxy_auth_visible": False}


def test_authenticated_proxy_rejects_forwarded_chains(monkeypatch):
    token = "proxy-boundary-secret-0123456789abcdef"
    monkeypatch.setenv("AURA_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("LSS_TRUSTED_PROXY_TOKEN", token)
    with TestClient(_app()) as client:
        response = client.get(
            "/identity",
            headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.4", "X-Forwarded-Proto": "https", "X-ESP-Proxy-Auth": token},
        )
    assert response.status_code == 400
    assert response.json()["security_gate"] == "trusted_proxy_client"
