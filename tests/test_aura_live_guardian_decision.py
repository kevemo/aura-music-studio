from datetime import UTC, datetime

from aura_music_studio.aura_live_guardian_decision import decide_with_creator_policy
from aura_music_studio.aura_live_guardian_policy import AuraLiveGuardianPolicy
from aura_music_studio.aura_live_moderator import AuraModeratorAuthorization, ModerationAction, ModerationMode, ModerationSignal, TikTokLiveConnectorCapabilities


def _authorization(mode=ModerationMode.AUTO_PROTECT):
    return AuraModeratorAuthorization(creator_handle="creator.live", creator_consent=True, moderator_assignment_confirmed=True, mode=mode, provider_write_enabled=True)


def _capabilities():
    return TikTokLiveConnectorCapabilities(approved_transport=True, can_read_live_comments=True, can_warn=True, can_mute=True, can_block=True, can_post_as_moderator=True)


def _policy(*, phrases=(), categories=frozenset({"spam", "harassment", "threat", "doxxing", "grooming_concern"})):
    return AuraLiveGuardianPolicy(user_id="creator-1", blocked_phrases=tuple(phrases), language_tolerance="balanced", spam_sensitivity="medium", enabled_categories=categories, updated_at=datetime.now(UTC), updated_by="member:creator-1")


def test_blocked_phrase_strengthens_lower_risk_rule_even_when_original_category_disabled():
    result = decide_with_creator_policy(
        authorization=_authorization(), capabilities=_capabilities(),
        signal=ModerationSignal(category="other", confidence=0.4, severity=0),
        policy=_policy(phrases=("no drama",), categories=frozenset({"threat", "doxxing", "grooming_concern"})),
        message="Please keep the NO DRAMA comments out of here",
    )
    assert result.signal.category == "creator_defined"
    assert result.signal.confidence == 0.99
    assert result.signal.severity == 1
    assert result.matched_blocked_phrases == ("no drama",)
    assert result.decision.action is ModerationAction.WARN
    assert result.category_suppressed_by_creator is False


def test_creator_can_suppress_noncritical_category_without_creating_write_authority():
    result = decide_with_creator_policy(
        authorization=_authorization(), capabilities=_capabilities(),
        signal=ModerationSignal(category="sexual", confidence=0.95, severity=2),
        policy=_policy(categories=frozenset({"threat", "doxxing", "grooming_concern"})),
    )
    assert result.category_suppressed_by_creator is True
    assert result.decision.action is ModerationAction.OBSERVE
    assert result.decision.provider_write_permitted is False
    assert result.decision.requires_human_confirmation is True


def test_critical_threat_cannot_be_suppressed():
    result = decide_with_creator_policy(
        authorization=_authorization(), capabilities=_capabilities(),
        signal=ModerationSignal(category="threat", confidence=0.96, severity=4),
        policy=_policy(categories=frozenset()),
    )
    assert result.decision.action is ModerationAction.ESCALATE
    assert result.decision.provider_write_permitted is False
    assert result.decision.requires_human_confirmation is True


def test_policy_never_bypasses_advisory_mode():
    auth = AuraModeratorAuthorization(creator_handle="creator.live", creator_consent=True, moderator_assignment_confirmed=True, mode=ModerationMode.ADVISORY, provider_write_enabled=False)
    result = decide_with_creator_policy(authorization=auth, capabilities=_capabilities(), signal=ModerationSignal(category="spam", confidence=0.9, severity=2), policy=_policy())
    assert result.decision.action is ModerationAction.RECOMMEND_MUTE
    assert result.decision.provider_write_permitted is False
    assert result.decision.requires_human_confirmation is True
