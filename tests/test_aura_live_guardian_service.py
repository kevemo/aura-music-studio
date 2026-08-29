from datetime import UTC, datetime

from aura_music_studio.aura_live_guardian_policy import AuraLiveGuardianPolicy
from aura_music_studio.aura_live_guardian_service import decide_and_record_guardian_event
from aura_music_studio.aura_live_moderator import (
    AuraModeratorAuthorization,
    ModerationAction,
    ModerationMode,
    ModerationSignal,
    TikTokLiveConnectorCapabilities,
)
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


def _authorization():
    return AuraModeratorAuthorization(
        creator_handle="creator.live",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        mode=ModerationMode.AUTO_PROTECT,
        provider_write_enabled=True,
    )


def _capabilities():
    return TikTokLiveConnectorCapabilities(
        approved_transport=True,
        can_read_live_comments=True,
        can_warn=True,
        can_mute=True,
        can_block=True,
        can_post_as_moderator=True,
    )


def _policy():
    return AuraLiveGuardianPolicy(
        user_id="creator-1",
        blocked_phrases=("private blocked phrase",),
        language_tolerance="balanced",
        spam_sensitivity="medium",
        enabled_categories=frozenset({"spam", "threat", "doxxing", "grooming_concern"}),
        updated_at=datetime.now(UTC),
        updated_by="member:creator-1",
    )


def test_guardian_audit_records_bounded_decision_without_raw_message_or_phrase(tmp_path):
    store = AuraLiveModeratorStore(tmp_path / "guardian.sqlite3")
    audited = decide_and_record_guardian_event(
        store=store,
        user_id="creator-1",
        authorization=_authorization(),
        capabilities=_capabilities(),
        signal=ModerationSignal(category="other", confidence=0.4, severity=0, evidence="raw classifier evidence"),
        policy=_policy(),
        message="viewer wrote PRIVATE BLOCKED PHRASE in the chat",
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


def test_high_risk_escalation_uses_human_escalation_audit_event(tmp_path):
    store = AuraLiveModeratorStore(tmp_path / "guardian.sqlite3")
    audited = decide_and_record_guardian_event(
        store=store,
        user_id="creator-1",
        authorization=_authorization(),
        capabilities=_capabilities(),
        signal=ModerationSignal(category="doxxing", confidence=0.99, severity=4, evidence="sensitive detail"),
        policy=_policy(),
        message="sensitive raw message",
    )
    assert audited.result.decision.action is ModerationAction.ESCALATE
    assert audited.audit_event.event_type == "human_escalation"
    assert audited.audit_event.metadata["provider_write_permitted"] is False
    assert "sensitive raw message" not in str(audited.audit_event.metadata)
    assert "sensitive detail" not in str(audited.audit_event.metadata)
