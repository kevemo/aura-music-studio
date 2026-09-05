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
    return tuple(phrase for phrase in policy.blocked_phrases if phrase.casefold() in normalized)


def decide_with_creator_policy(*, authorization: AuraModeratorAuthorization,
                               capabilities: TikTokLiveConnectorCapabilities,
                               signal: ModerationSignal,
                               policy: AuraLiveGuardianPolicy | None = None,
                               message: str = "",
                               moderator: AuraLiveModerator | None = None) -> GuardianModerationResult:
    """Apply creator preferences while preserving critical safety and provider-write boundaries."""
    engine = moderator or AuraLiveModerator()
    matches = _matches(policy, message)

    if signal.category in _CRITICAL_CATEGORIES:
        return GuardianModerationResult(
            signal=signal,
            decision=engine.decide(authorization, signal, capabilities),
            matched_blocked_phrases=matches,
            creator_policy_applied=policy is not None,
        )

    if matches:
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

    if policy is not None and signal.category not in policy.enabled_categories:
        return GuardianModerationResult(
            signal=signal,
            decision=ModerationDecision(
                action=ModerationAction.OBSERVE,
                public_response_allowed=False,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="Creator moderation policy disables automated handling for this non-critical category.",
            ),
            creator_policy_applied=True,
            category_suppressed_by_creator=True,
        )

    return GuardianModerationResult(
        signal=signal,
        decision=engine.decide(authorization, signal, capabilities),
        creator_policy_applied=policy is not None,
    )


__all__ = ["GuardianModerationResult", "decide_with_creator_policy"]
