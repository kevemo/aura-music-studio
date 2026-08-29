from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class RequestStub:
    def __init__(self, *, member=None):
        self.state = SimpleNamespace(member=member)
        self.headers = {}


def test_relay_setup_page_requires_member_and_drives_real_routes():
    from aura_music_studio import aura_live_overlay_connector as connector

    with pytest.raises(HTTPException) as denied:
        connector.connector_setup_page(RequestStub())
    assert denied.value.status_code == 401

    response = connector.connector_setup_page(RequestStub(member=SimpleNamespace(user_id="creator-1")))
    body = response.body.decode("utf-8")
    for path in (
        "/api/live-overlays/connector",
        "/api/live-overlays/connector/rotate",
        "/api/live-overlays/connector/disable",
        "/live-overlay-studio",
    ):
        assert path in body
    assert "Authorization: Bearer" in body
    assert "External dependency" in body
    assert "does not connect TikTok by itself" in body
    assert "does not grant moderation-write authority" in body
    assert "Connected to TikTok" not in body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_status_exposes_setup_navigation_but_never_secret(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_connector as connector

    db = tmp_path / "relay-ui.sqlite3"
    monkeypatch.setattr(connector, "DB_PATH", db)
    connector._init_schema()
    response = connector.connector_status(RequestStub(member=SimpleNamespace(user_id="creator-1")))
    payload = json.loads(response.body)
    assert payload["setup_path"] == "/live-overlay-studio/provider-relay"
    assert payload["provider_connection_state"] == "external_dependency"
    assert payload["direct_tiktok_connection_claimed"] is False
    assert payload["provider_moderation_authority_claimed"] is False
    serialized = json.dumps(payload).lower()
    assert '"token"' not in serialized
    assert "bearer " not in serialized
