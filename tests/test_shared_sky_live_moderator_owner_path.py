from __future__ import annotations

import aura_music_studio.shared_sky_live_moderator_permissions as permissions


def test_owner_assignment_actor_does_not_require_normal_member_session(monkeypatch):
    request = object()
    monkeypatch.setattr(permissions, "owner_session_authorized", lambda _request: True)
    monkeypatch.setattr(permissions, "_owner_actor", lambda _request: "owner-kev")

    def member_should_not_run(_request):
        raise AssertionError("Owner assignment must not require a normal member session")

    monkeypatch.setattr(permissions, "_member_actor", member_should_not_run)
    assert permissions._assignment_actor(request) == ("owner-kev", True)


def test_creator_assignment_actor_uses_active_member_path_when_not_owner(monkeypatch):
    request = object()
    monkeypatch.setattr(permissions, "owner_session_authorized", lambda _request: False)
    monkeypatch.setattr(permissions, "_member_actor", lambda _request: "creator-1")
    assert permissions._assignment_actor(request) == ("creator-1", False)
