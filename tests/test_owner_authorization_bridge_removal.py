from __future__ import annotations

from http.cookies import SimpleCookie
from pathlib import Path

from starlette.requests import Request

from aura_music_studio import (
    admin_portal,
    owner_auth,
    owner_backup_portal,
    owner_compute_portal,
    owner_users_portal,
)
from aura_music_studio.owner_authorization_migration import install_owner_authorization_migration


def _request(token: str | None = None, *, method: str = "GET", path: str = "/owner/test") -> Request:
    headers = []
    if token is not None:
        headers.append((b"cookie", f"lss_admin_session={token}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "owner-auth.sqlite3"))
    monkeypatch.setenv("LSS_ADMIN_KEY", "deployment-owner-key")
    monkeypatch.setenv("LSS_OWNER_MFA_REQUIRED", "false")
    monkeypatch.setenv("LSS_COOKIE_SECURE", "false")
    owner_auth._sessions = None
    install_owner_authorization_migration()


def _owner_cookie(response) -> str:
    jar = SimpleCookie()
    for value in response.headers.getlist("set-cookie"):
        jar.load(value)
    morsel = jar.get(owner_auth.OWNER_COOKIE)
    assert morsel is not None
    return morsel.value


def test_every_live_owner_surface_accepts_opaque_session_and_rejects_raw_key(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    opaque = owner_auth.sessions().create()
    assert opaque != "deployment-owner-key"

    opaque_request = _request(opaque)
    raw_key_request = _request("deployment-owner-key")
    no_cookie_request = _request()

    for authorized in (
        admin_portal._authorized,
        owner_users_portal._authorized,
        owner_backup_portal._authorized,
        owner_compute_portal._authorized,
    ):
        assert authorized(opaque_request) is True
        assert authorized(raw_key_request) is False
        assert authorized(no_cookie_request) is False


def test_legacy_admin_login_delegates_to_secure_opaque_session_flow(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    request = _request(method="POST", path="/owner/login")

    response = admin_portal.owner_login_submit(
        request,
        admin_key="deployment-owner-key",
        persona="",
        totp_code="",
    )

    token = _owner_cookie(response)
    assert token != "deployment-owner-key"
    assert owner_auth.sessions().valid(token, touch=False) is True


def test_owner_key_is_bootstrap_only_not_a_browser_session(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    assert owner_auth.owner_key_matches("deployment-owner-key") is True
    assert owner_auth.owner_authorized(_request("deployment-owner-key")) is False


def test_secret_substitution_bridge_is_absent_from_production_and_owner_auth_source():
    app_source = Path("app.py").read_text(encoding="utf-8")
    auth_source = Path("aura_music_studio/owner_auth.py").read_text(encoding="utf-8")
    admin_source = Path("aura_music_studio/admin_portal.py").read_text(encoding="utf-8")

    assert "OwnerLegacyCompatibilityMiddleware" not in app_source
    assert "OwnerLegacyCompatibilityMiddleware" not in auth_source
    assert "request.cookies[OWNER_COOKIE]" not in auth_source
    assert "owner_key_matches(token)" not in auth_source

    assert "install_owner_authorization_migration()" in app_source
    assert "owner_authorized(request)" in admin_source
    assert "compare_digest" not in admin_source
    assert "response.set_cookie" not in admin_source
    assert "secure_owner_login_submit" in admin_source
