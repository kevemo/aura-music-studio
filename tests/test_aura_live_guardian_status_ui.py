from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_guardian import router
from aura_music_studio.aura_live_guardian_status_ui import live_guardian_status


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def test_status_requires_authenticated_member():
    with pytest.raises(HTTPException) as exc:
        live_guardian_status(RequestStub())
    assert exc.value.status_code == 401


def test_status_response_is_private_and_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    response = live_guardian_status(RequestStub(SimpleNamespace(user_id="creator-1")))
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert b'"provider_execution_ready":false' in response.body
    assert b'"safety_state":"attention"' in response.body


def test_status_route_is_on_flat_production_guardian_router():
    paths = [route.path for route in router.routes]
    assert "/live-guardian/status" in paths
    assert "/live-guardian/readiness" in paths
    assert "/live-guardian/review" in paths
