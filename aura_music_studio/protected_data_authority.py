from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

from .audit import AuditLedger
from .owner_identity import OwnerPersona, owner_session_authorized, request_owner_persona

DecisionReason = Literal[
    "allowed",
    "owner_session_required",
    "owner_persona_required",
    "principal_not_admitted",
    "audit_unavailable",
]


@dataclass(frozen=True)
class ProtectedDataDecision:
    allowed: bool
    reason: DecisionReason
    persona: OwnerPersona | None = None


def _admitted_personas() -> frozenset[str]:
    """Return the deployment-controlled protected-data principals.

    This deliberately has no permissive default. A deployment must explicitly admit the
    Mary/Kev owner personas before cross-user protected data can be read.
    """

    raw = (os.getenv("AURA_SEC_PROTECTED_DATA_PERSONAS") or "").strip()
    return frozenset(
        part.strip().lower()
        for part in raw.split(",")
        if part.strip().lower() in {"mary", "kev"}
    )


def evaluate_protected_data_access(request: Request) -> ProtectedDataDecision:
    if not owner_session_authorized(request):
        return ProtectedDataDecision(False, "owner_session_required")

    persona = request_owner_persona(request)
    if persona is None:
        return ProtectedDataDecision(False, "owner_persona_required")

    if persona not in _admitted_personas():
        return ProtectedDataDecision(False, "principal_not_admitted", persona)

    return ProtectedDataDecision(True, "allowed", persona)


def authorize_protected_data_access(
    request: Request,
    *,
    action: str,
    subject_user_id: str | None = None,
    audit: AuditLedger | None = None,
) -> ProtectedDataDecision:
    """Authorize and audit a cross-user protected-data operation before it occurs.

    Successful access is fail-closed on audit availability: if the security ledger cannot
    record the read, the operation is denied. Denials are best-effort audited without ever
    turning an audit failure into access.
    """

    decision = evaluate_protected_data_access(request)
    ledger = audit or AuditLedger()
    details = {
        "protected_action": action[:160],
        "decision": decision.reason,
        "persona": decision.persona,
    }

    if not decision.allowed:
        try:
            ledger.append(
                actor="Aura Sec Protected Data Authority",
                action="protected_data_access_denied",
                subject_user_id=subject_user_id,
                details=details,
            )
        except Exception:
            pass
        return decision

    try:
        ledger.append(
            actor=f"{decision.persona.title()} · ESP Protected Data Authority",
            action="protected_data_access_granted",
            subject_user_id=subject_user_id,
            details=details,
        )
    except Exception:
        return ProtectedDataDecision(False, "audit_unavailable", decision.persona)

    return decision


def approved_protected_record_target(*, provider: str, target_id: str) -> bool:
    """Admit only the deployment-configured protected-record destination.

    The destination identifier remains server-side. This primitive is intentionally exact
    match and fail-closed so later Drive/backup writers cannot silently redirect personal
    records to arbitrary folders.
    """

    provider = (provider or "").strip().lower()
    target_id = (target_id or "").strip()
    if provider != "google_drive" or not target_id:
        return False
    configured = (os.getenv("AURA_SEC_PROTECTED_DATA_DRIVE_FOLDER_ID") or "").strip()
    return bool(configured and target_id == configured)
