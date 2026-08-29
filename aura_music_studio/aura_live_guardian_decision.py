from __future__ import annotations

from dataclasses import dataclass

from .aura_live_guardian_policy import AuraLiveGuardianPolicy
from .aura_live_moderator import (
    AuraLiveModerator,
    AuraModeratorAuthorization,
    ModerationAction,
    ModerationDecision,
    ModerationSignal,
    TikTokLiveConnectorCapabilities,
)

_CRITICAL_CATEGORIES = {"threat", "doxxing", "grooming_concern"}


@dataclass(frozen=True)
class GuardianModerationResult:
    signal: ModerationSignal
    decision: ModerationDecision
    matched_blocked_phrases: tuple[str, ...] = ()
    creator_policy_applied: bool = False
    category_suppressed_by_creator: bool = False


def _matches(policy: AuraLiveGuardianPolicy | None, message: str) -> tuple[str, ...]:
    if policy is None or not message:
        return ()
    normalized = " ".join(message.casefold().split())
    return tuple(
        phrase for phrase in policy.blocked_phrases if phrase.casefold() in normalized
    )


def decide_with_creator_policy(
    *,
    authorization: AuraModeratorAuthorization,
    capabilities: TikTokLiveConnectorCapabilities,
    signal: ModerationSignal,
    policy: AuraLiveGuardianPolicy | None = None,
    message: str = "",
    moderator: AuraLiveModerator | None = None,
) -> GuardianModerationResult:
    """Apply creator preferences before the final provider-write authorization boundary.

    Creator configuration may add a room-specific blocked-phrase signal or suppress a non-critical
    category recommendation. It can never suppress threat, doxxing, or grooming escalation, and it
    cannot grant provider authority. The existing AuraLiveModerator remains the final write gate.
    """

    engine = moderator or AuraLiveModerator()
    matches = _matches(policy, message)
    effective = signal

    if signal.category in _CRITICAL_CATEGORIES:
        # Critical categories always pass through unchanged so creator policy cannot weaken them.
        return GuardianModerationResult(
            signal=effective,
            decision=engine.decide(authorization, effective, capabilities),
            matched_blocked_phrases=matches,
            creator_policy_applied=policy is not None,
        )

    if policy is not None and signal.category not in policy.enabled_categories:
        # A creator may choose not to action lower-risk categories. This is recommendation-only and
        # does not alter the underlying classifier evidence supplied to audit/reporting layers.
        return GuardianModerationResult(
            signal=effective,
            decision=ModerationDecision(
                action=ModerationAction.OBSERVE,
                public_response_allowed=False,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="Creator moderation policy disables automated handling for this non-critical category.",
            ),
            matched_blocked_phrases=matches,
            creator_policy_applied=True,
            category_suppressed_by_creator=True,
        )

    if matches:
        # A creator-authored blocked phrase is a deterministic room rule. It can strengthen a
        # lower-risk recommendation but never overrides critical signals (handled above).
        effective = ModerationSignal(
            category="creator_defined",
            confidence=max(signal.confidence, 0.99),
            severity=max(signal.severity, 1),
            evidence="Creator-defined blocked phrase matched.",
        )

    return GuardianModerationResult(
        signal=effective,
        decision=engine.decide(authorization, effective, capabilities),
        matched_blocked_phrases=matches,
        creator_policy_applied=policy is not None,
    )


__all__ = ["GuardianModerationResult", "decide_with_creator_policy"]
