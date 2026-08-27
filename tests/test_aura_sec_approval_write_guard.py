from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.aura_sec_approval_write_guard import AuraSecApprovalWriteGuardMiddleware


def _client():
    app = FastAPI()

    @app.post("/aura-sec/approval/action-1/start")
    def portal_start():
        return {"ok": True}

    @app.post("/api/aura-sec/member/actions/action-1/approval-challenge")
    def api_challenge():
        return {"ok": True}

    @app.get("/aura-sec/approval/action-1")
    def portal_get():
        return {"ok": True}

    app.add_middleware(AuraSecApprovalWriteGuardMiddleware)
    return TestClient(app)


def test_browser_approval_post_requires_same_origin_evidence():
    client = _client()
    response = client.post("/aura-sec/approval/action-1/start")
    assert response.status_code == 403
    assert response.json()["command_issued"] is False


def test_browser_approval_post_allows_exact_same_origin():
    client = _client()
    response = client.post(
        "/aura-sec/approval/action-1/start",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200


def test_default_port_serialization_is_normalized():
    client = _client()
    response = client.post(
        "/aura-sec/approval/action-1/start",
        headers={"Origin": "http://testserver:80", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200


def test_browser_approval_post_rejects_cross_origin_and_same_site_subdomain():
    client = _client()
    assert client.post(
        "/aura-sec/approval/action-1/start",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    ).status_code == 403
    assert client.post(
        "/aura-sec/approval/action-1/start",
        headers={"Origin": "http://other.testserver", "Sec-Fetch-Site": "same-site"},
    ).status_code == 403


def test_same_host_cross_scheme_is_not_same_origin():
    client = _client()
    response = client.post(
        "/aura-sec/approval/action-1/start",
        headers={"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 403
    assert response.json()["command_issued"] is False


def test_referer_is_accepted_as_compatibility_fallback():
    client = _client()
    response = client.post(
        "/aura-sec/approval/action-1/start",
        headers={"Referer": "http://testserver/aura-sec/approval/action-1"},
    )
    assert response.status_code == 200


def test_approval_api_post_requires_explicit_bearer_token():
    client = _client()
    response = client.post("/api/aura-sec/member/actions/action-1/approval-challenge")
    assert response.status_code == 403
    assert response.json()["command_issued"] is False
    assert client.post(
        "/api/aura-sec/member/actions/action-1/approval-challenge",
        headers={"Authorization": "Bearer explicit-session-token"},
    ).status_code == 200


def test_read_only_review_get_is_not_blocked_by_write_guard():
    assert _client().get("/aura-sec/approval/action-1").status_code == 200
