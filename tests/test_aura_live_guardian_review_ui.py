from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_guardian_review import AuraLiveGuardianReviewStore
from aura_music_studio.aura_live_guardian_review_ui import (
    acknowledge_guardian_escalation,
    confirm_guardian_review,
    dismiss_guardian_review,
    guardian_review_page,
    router,
)
from aura_music_studio.aura_live_moderator import ModerationAction, ModerationDecision


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member(user_id="creator-review-1"):
    return SimpleNamespace(user_id=user_id, display_name="Creator")


def _decision(action, *, provider=False):
    return ModerationDecision(
        action=action,
        provider_write_permitted=provider,
        requires_human_confirmation=True,
        reason="bounded test recommendation",
    )


def _seed_action(store, user_id="creator-review-1"):
    return store.enqueue(
        user_id=user_id,
        audit_event_id=f"audit-action-{user_id}",
        signal_category="spam",
        signal_severity=2,
        confidence_bucket="high",
        decision=_decision(ModerationAction.RECOMMEND_MUTE, provider=True),
    )


def _seed_escalation(store, user_id="creator-review-1"):
    return store.enqueue(
        user_id=user_id,
        audit_event_id=f"audit-escalation-{user_id}",
        signal_category="doxxing",
        signal_severity=4,
        confidence_bucket="very_high",
        decision=_decision(ModerationAction.ESCALATE),
    )


def test_review_routes_are_private_and_no_public_source_route():
    paths = [route.path for route in router.routes]
    assert "/live-guardian/review" in paths
    assert "/live-guardian/review/{review_id}/confirm" in paths
    assert "/live-guardian/review/{review_id}/dismiss" in paths
    assert "/live-guardian/review/{review_id}/acknowledge" in paths
    assert not any("source" in path for path in paths)


def test_review_router_is_mounted_through_production_guardian_router():
    from aura_music_studio.aura_live_guardian import router as guardian_router
    paths = [route.path for route in guardian_router.routes]
    assert "/live-guardian/review" in paths
    assert "/live-guardian/review/{review_id}/confirm" in paths


def test_review_page_requires_authenticated_member(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    with pytest.raises(HTTPException) as exc:
        guardian_review_page(RequestStub())
    assert exc.value.status_code == 401


def test_review_page_shows_bounded_evidence_and_provider_truth(monkeypatch, tmp_path):
    db = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(db))
    store = AuraLiveGuardianReviewStore(db)
    item = _seed_action(store)
    assert item is not None
    response = guardian_review_page(RequestStub(_member()))
    body = response.body.decode("utf-8")
    assert "Human Review Queue" in body
    assert "Recommend Mute" in body
    assert "Spam" in body
    assert "Provider write was permitted at decision time:</b> Yes" in body
    assert "does not execute a TikTok action" in body
    assert "approved TikTok/partner connector boundary" in body
    assert response.headers["cache-control"] == "private, no-store"


def test_member_can_confirm_own_action_but_not_another_creators(monkeypatch, tmp_path):
    db = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(db))
    store = AuraLiveGuardianReviewStore(db)
    own = _seed_action(store)
    other = _seed_action(store, "creator-review-2")
    assert own is not None and other is not None

    response = confirm_guardian_review(RequestStub(_member()), own.review_id)
    assert response.status_code == 303
    assert store.get(user_id="creator-review-1", review_id=own.review_id).status == "confirmed"

    with pytest.raises(HTTPException) as exc:
        dismiss_guardian_review(RequestStub(_member()), other.review_id)
    assert exc.value.status_code == 404
    assert store.get(user_id="creator-review-2", review_id=other.review_id).status == "pending"


def test_escalation_must_use_acknowledgement_route(monkeypatch, tmp_path):
    db = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(db))
    store = AuraLiveGuardianReviewStore(db)
    item = _seed_escalation(store)
    assert item is not None

    with pytest.raises(HTTPException) as exc:
        confirm_guardian_review(RequestStub(_member()), item.review_id)
    assert exc.value.status_code == 409

    response = acknowledge_guardian_escalation(RequestStub(_member()), item.review_id)
    assert response.status_code == 303
    assert store.get(user_id="creator-review-1", review_id=item.review_id).status == "acknowledged"
