from datetime import UTC, datetime

from aura_music_studio.aura_live_guardian_policy import AuraLiveGuardianPolicy
from aura_music_studio.aura_live_guardian_service import decide_and_record_guardian_event
from aura_music_studio.aura_live_moderator import AuraModeratorAuthorization, ModerationAction, ModerationMode, ModerationSignal, TikTokLiveConnectorCapabilities
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


def _authorization(mode=ModerationMode.AUTO_PROTECT):
    return AuraModeratorAuthorization(creator_handle="creator.live", creator_consent=True, moderator_assignment_confirmed=True, mode=mode, provider_write_enabled=True)


def _capabilities():
    return TikTokLiveConnectorCapabilities(approved_transport=True, can_read_live_comments=True, can_warn=True, can_mute=True, can_block=True, can_post_as_moderator=True)


def _policy():
    return AuraLiveGuardianPolicy(user_id="creator-1", blocked_phrases=("private blocked phrase",), language_tolerance="balanced", spam_sensitivity="medium", enabled_categories=frozenset({"spam", "threat", "doxxing", "grooming_concern"}), updated_at=datetime.now(UTC), updated_by="member:creator-1")


def test_guardian_audit_records_bounded_decision_without_raw_content(tmp_path):
    store = AuraLiveModeratorStore(tmp_path / "guardian.sqlite3")
    audited = decide_and_record_guardian_event(
        store=store, user_id="creator-1", authorization=_authorization(), capabilities=_capabilities(),
        signal=ModerationSignal(category="other", confidence=0.4, severity=0, evidence="raw classifier evidence"),
        policy=_policy(), message="viewer wrote PRIVATE BLOCKED PHRASE in the chat",
    )
    assert audited.result.decision.action is ModerationAction.WARN
    metadata = audited.audit_event.metadata
    assert metadata["blocked_phrase_match_count"] == 1
    assert metadata["raw_message_persisted"] is False
    assert metadata["blocked_phrase_text_persisted"] is False
    serialized = str(metadata)
    assert "viewer wrote" not in serialized
    assert "private blocked phrase" not in serialized
    assert "raw classifier evidence" not in serialized
    assert store.verify_audit_chain("creator-1") is True
    # Auto Protect can avoid human review only when the existing bounded provider gate allows it.
    assert audited.review_item is None


def test_assisted_action_is_queued_for_human_confirmation(tmp_path):
    store = AuraLiveModeratorStore(tmp_path / "guardian.sqlite3")
    audited = decide_and_record_guardian_event(
        store=store, user_id="creator-1", authorization=_authorization(ModerationMode.ASSISTED), capabilities=_capabilities(),
        signal=ModerationSignal(category="spam", confidence=0.96, severity=2, evidence="do not persist me"),
        policy=_policy(), message="raw live comment must not persist",
    )
    assert audited.result.decision.action is ModerationAction.RECOMMEND_MUTE
    assert audited.result.decision.provider_write_permitted is True
    assert audited.result.decision.requires_human_confirmation is True
    assert audited.review_item is not None
    assert audited.review_item.review_kind == "action_confirmation"
    assert audited.review_item.provider_write_permitted_at_decision is True
    review_text = str(audited.review_item)
    assert "raw live comment" not in review_text
    assert "do not persist me" not in review_text


def test_high_risk_escalation_uses_human_escalation_audit_and_review_item(tmp_path):
    store = AuraLiveModeratorStore(tmp_path / "guardian.sqlite3")
    audited = decide_and_record_guardian_event(
        store=store, user_id="creator-1", authorization=_authorization(), capabilities=_capabilities(),
        signal=ModerationSignal(category="doxxing", confidence=0.99, severity=4, evidence="sensitive detail"),
        policy=_policy(), message="sensitive raw message",
    )
    assert audited.result.decision.action is ModerationAction.ESCALATE
    assert audited.audit_event.event_type == "human_escalation"
    assert audited.audit_event.metadata["provider_write_permitted"] is False
    assert audited.review_item is not None
    assert audited.review_item.review_kind == "safety_escalation"
    assert audited.review_item.expires_at is None
    assert "sensitive raw message" not in str(audited.audit_event.metadata)
    assert "sensitive detail" not in str(audited.audit_event.metadata)
    assert "sensitive raw message" not in str(audited.review_item)
    assert "sensitive detail" not in str(audited.review_item)
