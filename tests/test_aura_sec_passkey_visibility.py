from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.testclient import TestClient

import aura_music_studio.aura_sec_passkey_visibility as visibility
from aura_music_studio.aura_sec_passkey_visibility import AuraSecPasskeyVisibilityMiddleware


def _client():
    app = FastAPI()

    @app.get("/aura-sec/devices")
    def devices():
        return HTMLResponse(
            "<html><body><nav><a class='navitem' href='/aura-sec/settings'>Security Settings</a></nav>"
            "<div class='footer'>Footer</div></body></html>"
        )

    @app.get("/aura-sec/settings")
    def settings():
        return HTMLResponse(
            "<html><body><nav><a class='navitem active' href='/aura-sec/settings'>Security Settings</a></nav>"
            "<div class='footer'>Footer</div></body></html>"
        )

    @app.get("/aura-sec/already-linked")
    def already_linked():
        return HTMLResponse(
            "<html><body><nav><a class='navitem' href='/aura-sec/passkeys'>Passkeys &amp; High-Risk Auth</a>"
            "<a class='navitem' href='/aura-sec/settings'>Security Settings</a></nav>"
            "<div class='footer'>Footer</div></body></html>"
        )

    @app.get("/aura-sec/passkey-client.js")
    def script():
        return PlainTextResponse("const keep = true;", media_type="application/javascript")

    @app.get("/outside")
    def outside():
        return HTMLResponse("<a class='navitem' href='/aura-sec/settings'>Security Settings</a>")

    app.add_middleware(AuraSecPasskeyVisibilityMiddleware)
    return TestClient(app)


def _authenticated(monkeypatch):
    monkeypatch.setattr(
        visibility.accounts,
        "resolve_session",
        lambda token: {"id": "browser-member-1", "email": "browser@example.test"} if token else None,
    )


def test_security_navigation_exposes_passkey_management_before_settings():
    response = _client().get("/aura-sec/devices")
    assert response.status_code == 200
    text = response.text
    assert "href='/aura-sec/passkeys'" in text
    assert "Passkeys &amp; High-Risk Auth" in text
    assert text.index("/aura-sec/passkeys") < text.index("/aura-sec/settings")


def test_security_navigation_exposes_browser_guard_before_settings():
    response = _client().get("/aura-sec/devices")
    assert response.status_code == 200
    text = response.text
    assert "href='/aura-sec/browser-guard'" in text
    assert ">Browser Guard</a>" in text
    assert text.index("/aura-sec/browser-guard") < text.index("/aura-sec/settings")


def test_security_settings_explains_enforced_webauthn_policy_and_browser_guard_truth():
    response = _client().get("/aura-sec/settings")
    assert response.status_code == 200
    text = response.text
    assert "id='aura-sec-passkey-policy'" in text
    assert "WebAuthn step-up is enforced for destructive actions" in text
    assert "cannot downgrade to password-only authorization" in text
    assert "never receives the authenticator private key or biometric data" in text
    assert "browser approval still cannot issue or execute a native endpoint command" in text
    assert "Manage passkeys" in text
    assert "id='aura-sec-browser-guard-policy'" in text
    assert "Local-first link inspection is available now" in text
    assert "Automatic navigation protection remains unavailable" in text


def test_visibility_middleware_does_not_duplicate_existing_passkey_link():
    response = _client().get("/aura-sec/already-linked")
    assert response.status_code == 200
    assert response.text.count("href='/aura-sec/passkeys'") == 1
    assert response.text.count("href='/aura-sec/browser-guard'") == 1


def test_browser_guard_page_requires_member_session():
    response = _client().get("/aura-sec/browser-guard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signin?next=/aura-sec/browser-guard")


def test_browser_guard_status_is_truthful_and_authenticated(monkeypatch):
    _authenticated(monkeypatch)
    client = _client()
    denied = client.get("/api/aura-sec/member/browser-guard/status")
    assert denied.status_code == 401

    response = client.get(
        "/api/aura-sec/member/browser-guard/status",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["manual_link_inspection"] is True
    assert data["server_fetches_submitted_url"] is False
    assert data["cloud_reputation_configured"] is False
    assert data["automatic_navigation_protection"] is False
    assert data["extension_state"] == "not_released"
    assert data["signed_download_available"] is False


def test_browser_guard_manual_inspection_redacts_credentials_and_does_not_claim_cloud_reputation(monkeypatch):
    _authenticated(monkeypatch)
    client = _client()
    response = client.post(
        "/aura-sec/browser-guard",
        cookies={"lss_session": "test-session-token"},
        data={
            "url": "https://user:secret@example.com/account/login?utm_source=test",
            "source": "email",
        },
    )
    assert response.status_code == 200
    text = response.text
    assert "Local inspection result" in text
    assert "https://example.com/account/login?utm_source=test" in text
    assert "https://example.com/account/login" in text
    assert "secret" not in text
    assert "Embedded Credentials" in text
    assert "No cloud reputation claim is being made" in text
    assert "server did not visit the submitted destination" in text
    assert "Automatic extension protection" in text
    assert "Not released" in text


def test_browser_guard_rejects_ambiguous_link_without_echoing_it(monkeypatch):
    _authenticated(monkeypatch)
    response = _client().post(
        "/aura-sec/browser-guard",
        cookies={"lss_session": "test-session-token"},
        data={"url": "example.com/login", "source": "link"},
    )
    assert response.status_code == 200
    assert "Inspection rejected" in response.text
    assert "explicit URL scheme" in response.text
    assert "example.com/login" not in response.text
    assert "destination was not contacted" in response.text


def test_javascript_and_non_aura_pages_are_not_rewritten():
    client = _client()
    script = client.get("/aura-sec/passkey-client.js")
    assert script.text == "const keep = true;"
    outside = client.get("/outside")
    assert "href='/aura-sec/passkeys'" not in outside.text
    assert "href='/aura-sec/browser-guard'" not in outside.text
