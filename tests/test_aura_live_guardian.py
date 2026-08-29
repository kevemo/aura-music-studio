from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_guardian import (
    live_guardian,
    revoke_live_guardian,
    router,
    save_live_guardian_settings,
)
from aura_music_studio.aura_live_moderator import ModerationMode
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member():
    return SimpleNamespace(user_id="creator-guardian-1", display_name="Creator")


def test_guardian_routes_are_member_scoped_and_no_public_source_route():
    paths = [route.path for route in router.routes]
    assert "/live-guardian" in paths
    assert "/live-guardian/settings" in paths
    assert "/live-guardian/revoke" in paths
    assert not any("source" in path for path in paths)


def test_guardian_requires_authenticated_member(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    with pytest.raises(HTTPException) as exc:
        live_guardian(RequestStub())
    assert exc.value.status_code == 401


def test_creator_can_save_authorization_but_cannot_self_enable_provider_write(monkeypatch, tmp_path):
    db = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(db))
    response = save_live_guardian_settings(
        RequestStub(_member()),
        creator_handle="creator.live",
        mode=ModerationMode.AUTO_PROTECT,
        creator_consent=True,
        moderator_assignment_confirmed=True,
    )
    assert response.status_code == 303
    stored = AuraLiveModeratorStore(db).get(_member().user_id)
    assert stored is not None
    assert stored.authorization.creator_consent is True
    assert stored.authorization.moderator_assignment_confirmed is True
    assert stored.authorization.mode is ModerationMode.AUTO_PROTECT
    assert stored.authorization.provider_write_enabled is False


def test_guardian_page_exposes_official_aura_identity_tools_and_truthful_provider_state(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    save_live_guardian_settings(
        RequestStub(_member()),
        creator_handle="creator.live",
        mode=ModerationMode.ADVISORY,
        creator_consent=True,
        moderator_assignment_confirmed=False,
    )
    response = live_guardian(RequestStub(_member()))
    body = response.body.decode("utf-8")
    assert "@aura.chat.mod" in body
    assert "https://www.tiktok.com/@aura.chat.mod" in body
    assert "Overlay Studio" in body
    assert "Auto Cue Prompter" in body
    assert "Creators cannot self-enable provider writes" in body
    assert "Unavailable / recommendation only" in body
    assert "TikTok passwords" in body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_revoke_removes_current_authorization_but_preserves_audit(monkeypatch, tmp_path):
    db = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(db))
    save_live_guardian_settings(
        RequestStub(_member()),
        creator_handle="creator.live",
        mode=ModerationMode.ASSISTED,
        creator_consent=True,
        moderator_assignment_confirmed=True,
    )
    response = revoke_live_guardian(RequestStub(_member()))
    assert response.status_code == 303
    store = AuraLiveModeratorStore(db)
    assert store.get(_member().user_id) is None
    events = store.recent_events(_member().user_id)
    assert events[0].event_type == "authorization_revoked"
    assert store.verify_audit_chain(_member().user_id) is True
