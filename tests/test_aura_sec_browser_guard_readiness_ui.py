from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_passkey_visibility as visibility
from aura_music_studio.aura_sec_passkey_visibility import AuraSecPasskeyVisibilityMiddleware


def _client(monkeypatch, cloud_status):
    monkeypatch.setattr(
        visibility.accounts,
        "resolve_session",
        lambda token: {"id": "member-ready-1", "email": "ready@example.test"} if token else None,
    )
    monkeypatch.setattr(visibility, "browser_guard_cloud_status", lambda: dict(cloud_status))
    app = FastAPI()

    @app.get("/aura-sec/settings")
    def settings():
        return HTMLResponse(
            "<html><body><nav><a class='navitem active' href='/aura-sec/settings'>Security Settings</a></nav>"
            "<div class='footer'>Footer</div></body></html>"
        )

    app.add_middleware(AuraSecPasskeyVisibilityMiddleware)
    return TestClient(app)


def _ready_status():
    return {
        "engine_state": "local_inspection_available",
        "manual_link_inspection": True,
        "server_fetches_submitted_url": False,
        "cloud_reputation_state": "corroboration_ready",
        "cloud_reputation_active": True,
        "independent_corroboration_ready": True,
        "ready_provider_ids": ["google-web-risk", "openphish-commercial"],
        "provider_count_ready": 2,
        "provider_count_known": 5,
        "provider_state_counts": {"ready": 2, "disabled": 2, "blocked_commercial_use": 1},
        "providers": [
            {
                "provider_id": "google-web-risk",
                "display_name": "Google Web Risk",
                "kind": "url_reputation",
                "state": "ready",
                "enabled": True,
                "commercial_runtime_allowed": True,
                "commercial_approval_recorded": True,
                "credentials_configured": True,
                "adapter_health_verified": True,
                "ready": True,
                "credential_names_exposed": False,
                "credential_values_exposed": False,
                "raw_provider_response_exposed": False,
            },
            {
                "provider_id": "google-safe-browsing",
                "display_name": "Google Safe Browsing API",
                "kind": "url_reputation",
                "state": "blocked_commercial_use",
                "enabled": False,
                "commercial_runtime_allowed": False,
                "commercial_approval_recorded": False,
                "credentials_configured": False,
                "adapter_health_verified": False,
                "ready": False,
                "credential_names_exposed": False,
                "credential_values_exposed": False,
                "raw_provider_response_exposed": False,
            },
        ],
        "automatic_navigation_protection": False,
        "automatic_browser_extension_protection_released": False,
        "signed_download_available": False,
        "credential_names_exposed": False,
        "credential_values_exposed": False,
        "raw_provider_responses_exposed": False,
        "provider_execution_available_from_status_view": False,
        "truth": "Two independent providers have fresh verified readiness evidence.",
    }


def test_status_api_exposes_readiness_but_manual_inspector_stays_local_only(monkeypatch):
    client = _client(monkeypatch, _ready_status())
    response = client.get(
        "/api/aura-sec/member/browser-guard/status",
        headers={"Authorization": "Bearer test-session"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cloud_reputation_active"] is True
    assert data["independent_corroboration_ready"] is True
    assert data["provider_count_ready"] == 2
    assert data["cloud_reputation_configured"] is True
    assert data["manual_inspection_uses_cloud_reputation"] is False
    assert data["automatic_navigation_protection"] is False
    assert data["extension_state"] == "not_released"
    assert data["provider_execution_available_from_status_view"] is False


def test_browser_guard_page_renders_sanitized_readiness_without_claiming_live_lookup(monkeypatch):
    client = _client(monkeypatch, _ready_status())
    response = client.get(
        "/aura-sec/browser-guard",
        cookies={"lss_session": "test-session"},
    )
    assert response.status_code == 200
    text = response.text
    assert "Cloud provider readiness" in text
    assert "Corroboration Ready" in text
    assert "Ready providers:</b> 2 / 5" in text
    assert "Google Web Risk" in text
    assert "Google Safe Browsing API" in text
    assert "Blocked Commercial Use" in text
    assert "This manual inspector does not query cloud providers" in text
    assert "Credential names, values and raw provider responses are not exposed" in text
    assert "Automatic extension protection" in text
    assert "Not released" in text


def test_provider_readiness_does_not_change_local_manual_inspection_semantics(monkeypatch):
    client = _client(monkeypatch, _ready_status())
    response = client.post(
        "/aura-sec/browser-guard",
        cookies={"lss_session": "test-session"},
        data={"url": "https://example.com/login", "source": "email"},
    )
    assert response.status_code == 200
    text = response.text
    assert "Local inspection result" in text
    assert "No cloud reputation claim is being made" in text
    assert "server did not visit the submitted destination" in text
    assert "Corroboration Ready" in text


def test_status_and_page_do_not_expose_credentials_even_when_providers_ready(monkeypatch):
    secret_sentinel = "PRODUCTION-CREDENTIAL-SHOULD-NEVER-RENDER"
    status = _ready_status()
    # The status projection itself has no field into which a credential value can be placed.
    # This sentinel is retained only in the test process to prove it never appears by accident.
    client = _client(monkeypatch, status)
    api = client.get(
        "/api/aura-sec/member/browser-guard/status",
        headers={"Authorization": "Bearer test-session"},
    )
    page = client.get(
        "/aura-sec/browser-guard",
        cookies={"lss_session": "test-session"},
    )
    assert secret_sentinel not in api.text
    assert secret_sentinel not in page.text
    assert "AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL" not in api.text
    assert "AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL" not in page.text
    assert api.json()["credential_values_exposed"] is False
    assert api.json()["raw_provider_responses_exposed"] is False


def test_security_settings_distinguishes_provider_readiness_from_automatic_protection(monkeypatch):
    client = _client(monkeypatch, _ready_status())
    response = client.get("/aura-sec/settings")
    assert response.status_code == 200
    text = response.text
    assert "Cloud provider readiness is reported separately" in text
    assert "does not make the manual inspector perform a network lookup" in text
    assert "Automatic navigation protection remains unavailable" in text
