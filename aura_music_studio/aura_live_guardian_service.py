from __future__ import annotations

from dataclasses import dataclass

from .aura_live_guardian_decision import GuardianModerationResult, decide_with_creator_policy
from .aura_live_guardian_policy import AuraLiveGuardianPolicy
from .aura_live_guardian_review import AuraLiveGuardianReviewItem, AuraLiveGuardianReviewStore
from .aura_live_moderator import (
    AuraModeratorAuthorization,
    ModerationAction,
    ModerationSignal,
    TikTokLiveConnectorCapabilities,
)
from .aura_live_moderator_store import AuraLiveModerationAuditEvent, AuraLiveModeratorStore


@dataclass(frozen=True)
class AuditedGuardianDecision:
    result: GuardianModerationResult
    audit_event: AuraLiveModerationAuditEvent
    review_item: AuraLiveGuardianReviewItem | None = None


def _confidence_bucket(value: float) -> str:
    if value >= 0.95:
        return "very_high"
    if value >= 0.85:
        return "high"
    if value >= 0.70:
        return "moderate"
    return "below_action_threshold"


def decide_and_record_guardian_event(*, store: AuraLiveModeratorStore, user_id: str,
                                     authorization: AuraModeratorAuthorization,
                                     capabilities: TikTokLiveConnectorCapabilities,
                                     signal: ModerationSignal,
                                     policy: AuraLiveGuardianPolicy | None = None,
                                     message: str = "",
                                     actor: str = "aura:live-guardian") -> AuditedGuardianDecision:
    """Make a Guardian decision, audit it, and queue bounded human review when required.

    The review record contains no raw LIVE message or classifier evidence and confirming it never
    performs a provider write. Any later provider action must independently re-enter the approved
    connector/capability authorization boundary.
    """
    result = decide_with_creator_policy(
        authorization=authorization,
        capabilities=capabilities,
        signal=signal,
        policy=policy,
        message=message,
    )
    confidence_bucket = _confidence_bucket(result.signal.confidence)
    event_type = "human_escalation" if result.decision.action is ModerationAction.ESCALATE else "moderation_decision"
    audit = store.record_moderation_event(
        user_id=user_id,
        event_type=event_type,
        actor=actor,
        metadata={
            "signal_category": result.signal.category,
            "signal_severity": result.signal.severity,
            "confidence_bucket": confidence_bucket,
            "decision_action": result.decision.action.value,
            "provider_write_permitted": result.decision.provider_write_permitted,
            "requires_human_confirmation": result.decision.requires_human_confirmation,
            "creator_policy_applied": result.creator_policy_applied,
            "category_suppressed_by_creator": result.category_suppressed_by_creator,
            "blocked_phrase_match_count": len(result.matched_blocked_phrases),
            "raw_message_persisted": False,
            "blocked_phrase_text_persisted": False,
        },
    )

    review_store = AuraLiveGuardianReviewStore(store.database)
    review = review_store.enqueue(
        user_id=user_id,
        audit_event_id=audit.event_id,
        signal_category=result.signal.category,
        signal_severity=result.signal.severity,
        confidence_bucket=confidence_bucket,
        decision=result.decision,
    )
    return AuditedGuardianDecision(result=result, audit_event=audit, review_item=review)


__all__ = ["AuditedGuardianDecision", "decide_and_record_guardian_event"]
