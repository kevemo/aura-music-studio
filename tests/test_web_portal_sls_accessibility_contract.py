from __future__ import annotations

from starlette.requests import Request

import aura_music_studio.web_portal as portal
from aura_music_studio.native_products import SLS_PUBLIC_NAME


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


def _anonymous(monkeypatch) -> None:
    monkeypatch.setattr(portal.store, "resolve_session", lambda _token: None)


def test_unlimited_pro_public_feature_copy_keeps_sls_separate():
    copy = " | ".join(portal._feature_names("pro"))

    assert "Aura OS included" in copy
    assert SLS_PUBLIC_NAME in copy
    assert "licensed separately" in copy
    assert "Aura Sec" not in copy
    assert "SLS included" not in copy


def test_pricing_page_never_claims_membership_grants_sls(monkeypatch):
    _anonymous(monkeypatch)
    response = portal.pricing(_request("/pricing"))
    body = response.body.decode("utf-8")

    assert "Basic is £4.99/month" in body
    assert "Unlimited Pro is £9.99/month or £99/year" in body
    assert "includes Aura OS" in body
    assert SLS_PUBLIC_NAME in body
    assert "licensing is separate from Command Center membership" in body
    assert "Aura Sec" not in body


def test_public_page_shell_has_language_skip_link_main_and_primary_nav(monkeypatch):
    _anonymous(monkeypatch)
    response = portal.signin_page(_request("/signin"))
    body = response.body.decode("utf-8")

    assert "<html lang='en'>" in body
    assert "href='#main-content'>Skip to main content</a>" in body
    assert "<main id='main-content'>" in body
    assert "<nav class='nav' aria-label='Primary'>" in body
    assert ":focus-visible" in body


def test_signup_controls_have_explicit_labels_and_help_associations(monkeypatch):
    _anonymous(monkeypatch)
    response = portal.signup_page(_request("/signup"))
    body = response.body.decode("utf-8")

    expected = {
        "signup-display-name": "Name",
        "signup-email": "Email",
        "signup-password": "Password",
        "signup-plan": "Membership",
        "signup-billing-period": "Billing period",
    }
    for control_id, label in expected.items():
        assert f"<label for='{control_id}'>{label}</label>" in body
        assert f"id='{control_id}'" in body

    assert "aria-describedby='signup-password-help'" in body
    assert "id='signup-password-help'" in body
    assert "aria-describedby='signup-billing-help'" in body
    assert "id='signup-billing-help'" in body


def test_signin_controls_have_explicit_labels(monkeypatch):
    _anonymous(monkeypatch)
    response = portal.signin_page(_request("/signin"))
    body = response.body.decode("utf-8")

    assert "<label for='signin-email'>Email</label>" in body
    assert "id='signin-email'" in body
    assert "<label for='signin-password'>Password</label>" in body
    assert "id='signin-password'" in body
