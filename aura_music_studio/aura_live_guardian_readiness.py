from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .aura_live_guardian_policy import AuraLiveGuardianPolicyStore
from .aura_live_guardian_review import AuraLiveGuardianReviewStore
from .aura_live_moderator import ModerationMode
from .aura_live_moderator_store import AuraLiveModeratorStore

_MANDATORY_CATEGORIES = frozenset({"threat", "doxxing", "grooming_concern"})


@dataclass(frozen=True)
class AuraLiveReadinessCheck:
    key: str
    label: str
    passed: bool
    blocking: bool
    detail: str


@dataclass(frozen=True)
class AuraLiveReadinessReport:
    user_id: str
    pre_live_ready: bool
    provider_execution_ready: bool
    mode: str
    pending_reviews: int
    critical_escalations: int
    checks: tuple[AuraLiveReadinessCheck, ...]

    @property
    def blocking_failures(self) -> tuple[AuraLiveReadinessCheck, ...]:
        return tuple(check for check in self.checks if check.blocking and not check.passed)


def assess_live_readiness(*, database: str | Path, user_id: str) -> AuraLiveReadinessReport:
    """Build a truthful pre-LIVE readiness snapshot from persisted Guardian state.

    This assessment intentionally does not infer live TikTok/partner connector capability from
    browser state or historical approval. Provider execution therefore remains not-ready unless a
    separate runtime connector attestation is introduced by an approved integration layer.
    """
    moderator_store = AuraLiveModeratorStore(database)
    policy_store = AuraLiveGuardianPolicyStore(database)
    review_store = AuraLiveGuardianReviewStore(database)

    stored = moderator_store.get(user_id)
    authorization = stored.authorization if stored else None
    policy = policy_store.get(user_id)
    pending = review_store.pending(user_id, limit=200)
    critical = [item for item in pending if item.review_kind == "safety_escalation"]

    consent = bool(authorization and authorization.creator_consent)
    assignment = bool(authorization and authorization.moderator_assignment_confirmed)
    audit_ok = moderator_store.verify_audit_chain(user_id)
    if policy is None:
        mandatory_ok = True
        policy_detail = "Default Guardian policy applies; mandatory threat, doxxing and grooming protections remain active."
    else:
        mandatory_ok = _MANDATORY_CATEGORIES.issubset(policy.enabled_categories)
        policy_detail = (
            "Mandatory threat, doxxing and grooming protections are active."
            if mandatory_ok
            else "Mandatory safety policy integrity check failed."
        )

    mode = authorization.mode.value if authorization else ModerationMode.ADVISORY.value
    checks = (
        AuraLiveReadinessCheck(
            "authorization",
            "Guardian authorization configured",
            authorization is not None,
            True,
            "Save your TikTok handle and Guardian settings before going LIVE."
            if authorization is None
            else "Guardian authorization record is present.",
        ),
        AuraLiveReadinessCheck(
            "creator_consent",
            "Creator moderation consent",
            consent,
            True,
            "Explicit creator consent is active." if consent else "Explicit creator consent has not been granted.",
        ),
        AuraLiveReadinessCheck(
            "moderator_assignment",
            "Aura moderator assignment confirmed",
            assignment,
            True,
            "The creator confirms @aura.chat.mod is assigned through TikTok's supported moderation controls."
            if assignment
            else "Moderator assignment has not been confirmed.",
        ),
        AuraLiveReadinessCheck(
            "mandatory_safety",
            "Mandatory critical protections",
            mandatory_ok,
            True,
            policy_detail,
        ),
        AuraLiveReadinessCheck(
            "audit_integrity",
            "Moderation audit-chain integrity",
            audit_ok,
            True,
            "Hash-chained Guardian evidence verifies." if audit_ok else "Guardian audit-chain integrity verification failed.",
        ),
        AuraLiveReadinessCheck(
            "critical_reviews",
            "No unacknowledged critical escalations",
            not critical,
            True,
            "No critical Guardian safety escalations are waiting for acknowledgement."
            if not critical
            else f"{len(critical)} critical safety escalation(s) require acknowledgement.",
        ),
        AuraLiveReadinessCheck(
            "human_review",
            "Human review queue",
            not pending,
            False,
            "Human review queue is clear." if not pending else f"{len(pending)} item(s) currently require human review.",
        ),
        AuraLiveReadinessCheck(
            "provider_runtime",
            "Current provider connector attestation",
            False,
            False,
            "No current approved TikTok/partner connector capability attestation is asserted by this persisted readiness snapshot. Provider execution remains fail-closed.",
        ),
    )

    pre_live_ready = all(check.passed for check in checks if check.blocking)
    # Historical provider_write_enabled is authorization state, not proof that an approved provider
    # transport is connected and capable right now. Never promote it to runtime execution readiness.
    provider_execution_ready = False
    return AuraLiveReadinessReport(
        user_id=user_id,
        pre_live_ready=pre_live_ready,
        provider_execution_ready=provider_execution_ready,
        mode=mode,
        pending_reviews=len(pending),
        critical_escalations=len(critical),
        checks=checks,
    )


__all__ = ["AuraLiveReadinessCheck", "AuraLiveReadinessReport", "assess_live_readiness"]
