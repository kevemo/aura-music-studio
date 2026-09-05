from fastapi.testclient import TestClient

from aura_music_studio.api import app


def test_signin_surfaces_password_recovery_link():
    client = TestClient(app)
    response = client.get("/signin")
    assert response.status_code == 200
    assert "href='/auth/forgot-password'" in response.text


def test_forgot_password_page_is_public_but_not_cacheable_and_non_enumerating():
    client = TestClient(app)
    page = client.get("/auth/forgot-password")
    assert page.status_code == 200
    assert "Reset your password" in page.text
    assert "/auth/password-reset/request" in page.text
    assert page.headers.get("cache-control") == "no-store"
    assert "serviceWorker.register" not in page.text

    response = client.post(
        "/auth/password-reset/request",
        json={"email": "definitely-not-a-real-account@example.invalid"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert "If an eligible account exists" in payload["message"]
    assert "token" not in payload


def test_reset_page_reads_fragment_without_reflecting_request_data():
    client = TestClient(app)
    page = client.get("/auth/reset-password")
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    assert "serviceWorker.register" not in page.text
    assert "new URLSearchParams(window.location.hash.slice(1)).get('token')" in page.text
    assert "window.location.search" not in page.text
    assert "history.replaceState({},'', '/auth/reset-password')" in page.text
