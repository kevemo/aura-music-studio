from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .aura_live_guardian_readiness import assess_live_readiness


@dataclass(frozen=True)
class AuraLiveSessionStatus:
    user_id: str
    safety_state: str
    pre_live_ready: bool
    provider_execution_ready: bool
    pending_reviews: int
    critical_escalations: int
    audit_integrity_ok: bool
    mode: str
    message: str


def build_live_session_status(*, database: str | Path, user_id: str) -> AuraLiveSessionStatus:
    """Return a bounded creator-visible Guardian status snapshot.

    This is display state only. It cannot execute a moderation action, infer provider authority,
    or turn browser/session state into connector capability.
    """
    report = assess_live_readiness(database=database, user_id=user_id)
    audit = next(check for check in report.checks if check.key == "audit_integrity")

    if report.critical_escalations:
        state = "critical"
        message = "Critical Guardian safety escalation requires acknowledgement."
    elif not report.pre_live_ready:
        state = "attention"
        message = "Guardian safety prerequisites need attention before LIVE."
    elif report.pending_reviews:
        state = "review"
        message = "Guardian is ready for LIVE; human review items are waiting."
    else:
        state = "ready"
        message = "Guardian safety prerequisites are ready for LIVE."

    return AuraLiveSessionStatus(
        user_id=user_id,
        safety_state=state,
        pre_live_ready=report.pre_live_ready,
        provider_execution_ready=report.provider_execution_ready,
        pending_reviews=report.pending_reviews,
        critical_escalations=report.critical_escalations,
        audit_integrity_ok=audit.passed,
        mode=report.mode,
        message=message,
    )


__all__ = ["AuraLiveSessionStatus", "build_live_session_status"]
