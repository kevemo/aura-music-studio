from __future__ import annotations

from typing import Any

from .audit import AuditLedger
from .shared_contracts import OwnerOverrideEvidence


class AuditWriter:
    """Cross-domain writer that reuses the existing hash-chained, DLP-sanitised ledger."""

    def __init__(self, ledger: AuditLedger) -> None:
        self.ledger = ledger

    def write(
        self,
        *,
        actor_id: str,
        role: str,
        action: str,
        target_type: str,
        target_id: str,
        correlation_id: str,
        reason: str | None = None,
        previous_state: Any | None = None,
        new_state: Any | None = None,
        metadata: dict[str, Any] | None = None,
        override: OwnerOverrideEvidence | None = None,
    ) -> dict:
        details: dict[str, Any] = {
            "role": role,
            "target_type": target_type,
            "target_id": target_id,
            "correlation_id": correlation_id,
            "reason": reason or "",
            "previous_state": previous_state,
            "new_state": new_state,
            "metadata": metadata or {},
        }
        if override is not None:
            details["override"] = override.model_dump(mode="json")
        return self.ledger.append(
            actor=actor_id,
            action=action,
            subject_user_id=target_id if target_type == "user" else None,
            details=details,
        )

    def owner_override_callback(
        self, *, actor_id: str, target_type: str, target_id: str
    ):
        def callback(evidence: OwnerOverrideEvidence, purpose: str) -> None:
            self.write(
                actor_id=actor_id,
                role="owner",
                action="owner_override",
                target_type=target_type,
                target_id=target_id,
                correlation_id=evidence.correlation_id,
                reason=evidence.reason,
                metadata={"purpose": purpose},
                override=evidence,
            )
        return callback
