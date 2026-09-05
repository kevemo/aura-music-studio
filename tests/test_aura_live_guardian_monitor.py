from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_guardian_monitor import live_guardian_monitor
from aura_music_studio.aura_live_guardian_status_ui import live_guardian_status


class RequestStub:
    def __init__(self, member=None, query_params=None):
        self.state = SimpleNamespace(member=member)
        self.query_params = query_params or {}


def test_monitor_requires_authenticated_member():
    with pytest.raises(HTTPException) as exc:
        live_guardian_monitor(RequestStub())
    assert exc.value.status_code == 401


def test_monitor_is_private_same_origin_and_has_no_external_sdk():
    response = live_guardian_monitor(RequestStub(SimpleNamespace(user_id="creator-1")))
    body = response.body.decode("utf-8")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "fetch('/live-guardian/status'" in body
    assert "setInterval(refresh,3000)" in body
    assert "No provider authority is inferred." in body
    for external in ("firebase", "pusher", "ably", "socket.io", "segment", "google-analytics"):
        assert external not in body.lower()


def test_monitor_contains_only_bounded_guardian_status_fields():
    body = live_guardian_monitor(RequestStub(SimpleNamespace(user_id="creator-1"))).body.decode("utf-8")
    for forbidden in (
        "comment_text",
        "message_text",
        "blocked_phrase",
        "classifier_evidence",
        "access_token",
        "private_key",
        "password",
    ):
        assert forbidden not in body.lower()


def test_status_route_can_render_monitor_without_third_party_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    response = live_guardian_status(
        RequestStub(SimpleNamespace(user_id="creator-1"), {"view": "monitor"})
    )
    assert response.media_type == "text/html"
    assert b"Guardian Monitor" in response.body
    assert b"same-origin Aura server" in response.body
    assert b"Fail-closed" in response.body


def test_existing_status_json_contract_remains_default(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    response = live_guardian_status(RequestStub(SimpleNamespace(user_id="creator-1")))
    assert response.media_type == "application/json"
    assert b'"provider_execution_ready":false' in response.body
