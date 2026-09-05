from __future__ import annotations

from typing import Any

from .audit import AuditLogger
from .shared_contracts import OwnerOverrideEvidence


_SENSITIVE_MARKERS = (
    "password", "secret", "token", "authorization", "api_key", "apikey",
    "private_key", "credential",
)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in _SENSITIVE_MARKERS):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_sensitive(nested)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


class AuditWriter:
    """Canonical consequential-action writer backed by the existing hash chain."""

    def __init__(self, logger: AuditLogger) -> None:
        self.logger = logger

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
    ) -> dict[str, Any]:
        details = {
            "correlation_id": correlation_id,
            "reason": reason or "",
            "previous_state": redact_sensitive(previous_state),
            "new_state": redact_sensitive(new_state),
            "metadata": redact_sensitive(metadata or {}),
        }
        if override is not None:
            details["override"] = {
                "override_id": override.override_id,
                "owner_user_id": override.owner_user_id,
                "reason": override.reason,
                "approved_at": override.approved_at.isoformat(),
                "correlation_id": override.correlation_id,
            }
        return self.logger.append(
            actor_id=actor_id,
            role=role,
            action=action,
            target_type=target_type,
            target_id=target_id,
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
