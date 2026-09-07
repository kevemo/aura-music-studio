from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_guardian_readiness_ui import live_guardian_readiness_page, router
from aura_music_studio.aura_live_moderator import AuraModeratorAuthorization, ModerationMode
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member(user_id="creator-ready-1"):
    return SimpleNamespace(user_id=user_id, display_name="Creator")


def _authorize(database):
    AuraLiveModeratorStore(database).save_creator_authorization(
        user_id="creator-ready-1",
        actor="member:creator-ready-1",
        authorization=AuraModeratorAuthorization(
            creator_handle="creator.ready",
            creator_consent=True,
            moderator_assignment_confirmed=True,
            mode=ModerationMode.ASSISTED,
            provider_write_enabled=False,
        ),
    )


def test_readiness_page_requires_authenticated_member(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    with pytest.raises(HTTPException) as exc:
        live_guardian_readiness_page(RequestStub())
    assert exc.value.status_code == 401


def test_readiness_ui_is_private_and_truthful_about_provider_execution(monkeypatch, tmp_path):
    database = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(database))
    _authorize(database)
    response = live_guardian_readiness_page(RequestStub(_member()))
    body = response.body.decode("utf-8")
    assert "LIVE Safety Readiness" in body
    assert "Ready for LIVE" in body
    assert "Provider execution truth" in body
    assert "Fail-closed / not asserted by this page" in body
    assert "browser state or historical approval" in body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in response.headers["x-robots-tag"]


def test_readiness_route_is_mounted_on_flat_production_guardian_router():
    from aura_music_studio.aura_live_guardian import router as guardian_router

    standalone_paths = [route.path for route in router.routes]
    production_paths = [route.path for route in guardian_router.routes]
    assert "/live-guardian/readiness" in standalone_paths
    assert "/live-guardian/readiness" in production_paths
    assert all(hasattr(route, "path") for route in guardian_router.routes)
