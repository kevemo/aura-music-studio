from __future__ import annotations

from types import SimpleNamespace

import pytest

import aura_music_studio.shared_sky_live_moderator_actions as actions


def _body(action: str):
    return SimpleNamespace(action=action)


def test_delegated_moderator_can_only_use_session_scoped_moderation_actions(monkeypatch):
    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "moderator")
    monkeypatch.setattr(
        actions,
        "_BASE_MODERATE",
        lambda store, broadcast_id, actor_user_id, owner, body: {"action": body.action},
    )

    for allowed in ("delete_message", "timeout_user", "remove_user"):
        assert actions._limited_moderate(object(), "live-1", "mod-1", False, _body(allowed)) == {
            "action": allowed
        }

    for denied in (
        "pin_message",
        "unpin_message",
        "block_user",
        "unblock_user",
        "set_slow_mode",
        "set_chat_enabled",
        "set_followers_only",
        "set_members_only",
    ):
        with pytest.raises(PermissionError, match="Limited Moderators"):
            actions._limited_moderate(object(), "live-1", "mod-1", False, _body(denied))


def test_creator_and_owner_keep_broader_moderation_authority(monkeypatch):
    calls = []
    monkeypatch.setattr(
        actions,
        "_BASE_MODERATE",
        lambda store, broadcast_id, actor_user_id, owner, body: calls.append(
            (actor_user_id, owner, body.action)
        )
        or {"action": body.action},
    )

    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "creator")
    assert actions._limited_moderate(object(), "live-1", "creator-1", False, _body("block_user"))["action"] == "block_user"

    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "owner")
    assert actions._limited_moderate(object(), "live-1", None, True, _body("set_chat_enabled"))["action"] == "set_chat_enabled"
    assert calls == [
        ("creator-1", False, "block_user"),
        (None, True, "set_chat_enabled"),
    ]


def test_delegated_moderator_cannot_create_viewer_polls(monkeypatch):
    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "moderator")
    with pytest.raises(PermissionError, match="LIVE creator"):
        actions._creator_only_create_poll(object(), "live-1", "mod-1", object())

    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "creator")
    monkeypatch.setattr(
        actions,
        "_BASE_CREATE_POLL",
        lambda store, broadcast_id, actor_user_id, body: {"created_by": actor_user_id},
    )
    assert actions._creator_only_create_poll(object(), "live-1", "creator-1", object()) == {
        "created_by": "creator-1"
    }


def test_delegated_q_and_a_is_moderation_not_show_control(monkeypatch):
    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "moderator")
    monkeypatch.setattr(
        actions,
        "_BASE_MODERATE_QA",
        lambda store, broadcast_id, question_id, actor_user_id, body: {"action": body.action},
    )

    for allowed in ("approve", "reject", "remove"):
        assert actions._limited_moderate_qa(
            object(), "live-1", "question-1", "mod-1", _body(allowed)
        )["action"] == allowed

    for denied in ("select", "answered"):
        with pytest.raises(PermissionError, match="Creator control"):
            actions._limited_moderate_qa(
                object(), "live-1", "question-1", "mod-1", _body(denied)
            )


def test_unassigned_user_has_no_limited_action_authority(monkeypatch):
    monkeypatch.setattr(actions, "_authority_kind", lambda *args, **kwargs: "none")
    with pytest.raises(PermissionError):
        actions._limited_moderate(object(), "live-1", "viewer-1", False, _body("delete_message"))
    with pytest.raises(PermissionError):
        actions._limited_moderate_qa(
            object(), "live-1", "question-1", "viewer-1", _body("approve")
        )


def test_moderation_queue_projection_hides_reporter_identity():
    row = {
        "id": "report-1",
        "reporter_user_id": "private-reporter",
        "target_user_id": "target-1",
        "message_id": "msg-1",
        "category": "harassment",
        "reason": "Reported message",
        "state": "submitted",
        "evidence_json": '{"message":{"id":"msg-1","body":"evidence"}}',
        "created_at": "2026-09-05T03:00:00+00:00",
        "audit_id": "audit-1",
    }
    safe = actions._safe_report(row)
    assert "reporter_user_id" not in safe
    assert safe["reporter_identity_exposed"] is False
    assert safe["evidence"]["message"]["id"] == "msg-1"


def test_owner_review_actor_is_stable_without_normal_member_session(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace(member=None))
    monkeypatch.setattr(actions, "owner_session_authorized", lambda _request: True)
    assert actions._request_actor(request) == ("owner", True, "owner")


def test_wave5_bootstrap_mounts_limited_moderator_review_routes():
    from aura_music_studio import shared_sky_live_bootstrap as bootstrap

    signatures = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in bootstrap._LIVE_MODERATOR_ACTION_ROUTES
    }
    assert (
        "/shared-sky/live/api/watch/{broadcast_id}/moderation-queue",
        ("GET",),
    ) in signatures
    assert (
        "/shared-sky/live/api/watch/{broadcast_id}/reports/{report_id}/escalate",
        ("POST",),
    ) in signatures
    assert (
        "/shared-sky/live/api/watch/{broadcast_id}/flag-stream",
        ("POST",),
    ) in signatures
