from datetime import UTC, datetime, timedelta

import pytest

from aura_music_studio.aura_live_guardian_review import AuraLiveGuardianReviewStore
from aura_music_studio.aura_live_moderator import ModerationAction, ModerationDecision


def _decision(action: ModerationAction, *, human=True, provider=False):
    return ModerationDecision(
        action=action,
        provider_write_permitted=provider,
        requires_human_confirmation=human,
        reason="bounded test decision",
    )


def test_action_confirmation_is_short_lived_and_confirmation_is_only_state(tmp_path):
    store = AuraLiveGuardianReviewStore(tmp_path / "guardian.sqlite3")
    now = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    item = store.enqueue(
        user_id="creator-1", audit_event_id="audit-1", signal_category="spam",
        signal_severity=2, confidence_bucket="high",
        decision=_decision(ModerationAction.RECOMMEND_MUTE, provider=True), now=now,
    )
    assert item is not None
    assert item.review_kind == "action_confirmation"
    assert item.status == "pending"
    assert item.expires_at == now + timedelta(minutes=10)
    assert item.provider_write_permitted_at_decision is True

    confirmed = store.confirm_action(user_id="creator-1", review_id=item.review_id, actor="member:creator-1", now=now + timedelta(minutes=1))
    assert confirmed.status == "confirmed"
    assert confirmed.resolved_by == "member:creator-1"
    # The review record contains no execution/completion/provider receipt state; confirmation is intent only.
    assert not hasattr(confirmed, "provider_write_executed")
    assert not hasattr(confirmed, "provider_receipt")


def test_expired_action_cannot_be_confirmed_but_can_be_dismissed(tmp_path):
    store = AuraLiveGuardianReviewStore(tmp_path / "guardian.sqlite3")
    now = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    item = store.enqueue(
        user_id="creator-1", audit_event_id="audit-2", signal_category="harassment",
        signal_severity=1, confidence_bucket="high", decision=_decision(ModerationAction.WARN),
        now=now, action_ttl_seconds=30,
    )
    assert item is not None
    with pytest.raises(ValueError, match="expired"):
        store.confirm_action(user_id="creator-1", review_id=item.review_id, actor="member:creator-1", now=now + timedelta(seconds=31))
    dismissed = store.dismiss_action(user_id="creator-1", review_id=item.review_id, actor="member:creator-1", now=now + timedelta(seconds=31))
    assert dismissed.status == "dismissed"


def test_critical_escalation_requires_acknowledgement_and_never_expires(tmp_path):
    store = AuraLiveGuardianReviewStore(tmp_path / "guardian.sqlite3")
    item = store.enqueue(
        user_id="creator-1", audit_event_id="audit-3", signal_category="doxxing",
        signal_severity=4, confidence_bucket="very_high", decision=_decision(ModerationAction.ESCALATE),
    )
    assert item is not None
    assert item.review_kind == "safety_escalation"
    assert item.expires_at is None
    assert item.provider_write_permitted_at_decision is False
    with pytest.raises(ValueError, match="invalid"):
        store.confirm_action(user_id="creator-1", review_id=item.review_id, actor="member:creator-1")
    acknowledged = store.acknowledge_escalation(user_id="creator-1", review_id=item.review_id, actor="member:creator-1")
    assert acknowledged.status == "acknowledged"


def test_review_queue_is_tenant_scoped(tmp_path):
    store = AuraLiveGuardianReviewStore(tmp_path / "guardian.sqlite3")
    item = store.enqueue(
        user_id="creator-1", audit_event_id="audit-4", signal_category="spam",
        signal_severity=2, confidence_bucket="high", decision=_decision(ModerationAction.RECOMMEND_MUTE),
    )
    assert item is not None
    with pytest.raises(KeyError):
        store.get(user_id="creator-2", review_id=item.review_id)
    with pytest.raises(KeyError):
        store.dismiss_action(user_id="creator-2", review_id=item.review_id, actor="member:creator-2")
    assert len(store.pending("creator-1")) == 1
    assert store.pending("creator-2") == []


def test_duplicate_audit_event_is_idempotent_and_cross_tenant_reuse_fails(tmp_path):
    store = AuraLiveGuardianReviewStore(tmp_path / "guardian.sqlite3")
    first = store.enqueue(
        user_id="creator-1", audit_event_id="audit-5", signal_category="spam",
        signal_severity=1, confidence_bucket="high", decision=_decision(ModerationAction.WARN),
    )
    second = store.enqueue(
        user_id="creator-1", audit_event_id="audit-5", signal_category="spam",
        signal_severity=1, confidence_bucket="high", decision=_decision(ModerationAction.WARN),
    )
    assert first is not None and second is not None
    assert second.review_id == first.review_id
    with pytest.raises(ValueError, match="tenant mismatch"):
        store.enqueue(
            user_id="creator-2", audit_event_id="audit-5", signal_category="spam",
            signal_severity=1, confidence_bucket="high", decision=_decision(ModerationAction.WARN),
        )


def test_non_actionable_observe_does_not_enter_review_queue(tmp_path):
    store = AuraLiveGuardianReviewStore(tmp_path / "guardian.sqlite3")
    item = store.enqueue(
        user_id="creator-1", audit_event_id="audit-6", signal_category="other",
        signal_severity=0, confidence_bucket="below_action_threshold",
        decision=_decision(ModerationAction.OBSERVE),
    )
    assert item is None
    assert store.pending("creator-1") == []
