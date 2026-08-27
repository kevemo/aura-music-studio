from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.testclient import TestClient

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


def test_security_navigation_exposes_passkey_management_before_settings():
    response = _client().get("/aura-sec/devices")
    assert response.status_code == 200
    text = response.text
    assert "href='/aura-sec/passkeys'" in text
    assert "Passkeys &amp; High-Risk Auth" in text
    assert text.index("/aura-sec/passkeys") < text.index("/aura-sec/settings")


def test_security_settings_explains_enforced_webauthn_policy():
    response = _client().get("/aura-sec/settings")
    assert response.status_code == 200
    text = response.text
    assert "id='aura-sec-passkey-policy'" in text
    assert "WebAuthn step-up is enforced for destructive actions" in text
    assert "cannot downgrade to password-only authorization" in text
    assert "never receives the authenticator private key or biometric data" in text
    assert "browser approval still cannot issue or execute a native endpoint command" in text
    assert "Manage passkeys" in text


def test_visibility_middleware_does_not_duplicate_existing_passkey_link():
    response = _client().get("/aura-sec/already-linked")
    assert response.status_code == 200
    assert response.text.count("href='/aura-sec/passkeys'") == 1


def test_javascript_and_non_aura_pages_are_not_rewritten():
    client = _client()
    script = client.get("/aura-sec/passkey-client.js")
    assert script.text == "const keep = true;"
    outside = client.get("/outside")
    assert "href='/aura-sec/passkeys'" not in outside.text
