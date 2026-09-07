from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from pydantic import Field

from .shared_contracts import ContractModel, NonEmptyId


class CapabilityStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    FEATURE_FLAGGED = "feature_flagged"
    NOT_CONFIGURED = "not_configured"
    CREDENTIALS_MISSING = "credentials_missing"
    PLATFORM_APPROVAL_PENDING = "platform_approval_pending"
    USER_INELIGIBLE = "user_ineligible"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    DEGRADED = "degraded"


AVAILABLE_STATUSES = {CapabilityStatus.ENABLED, CapabilityStatus.DEGRADED}


class CapabilityRecord(ContractModel):
    key: NonEmptyId
    status: CapabilityStatus
    reason: str = Field(default="", max_length=1000)
    provider: str | None = Field(default=None, max_length=128)
    owner_enabled: bool = True
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86400)

    @property
    def available(self) -> bool:
        return self.owner_enabled and self.status in AVAILABLE_STATUSES


ProviderCapabilityState = CapabilityRecord


class CapabilityRegistry:
    """Server-owned capability snapshot. Client payloads are presentation-only."""

    def __init__(self, records: Iterable[CapabilityRecord] = ()) -> None:
        self._records = {record.key: record for record in records}

    def get(self, key: str) -> CapabilityRecord:
        try:
            return self._records[key]
        except KeyError as exc:
            raise KeyError(f"unknown capability {key!r}") from exc

    def require_available(self, key: str) -> CapabilityRecord:
        record = self.get(key)
        if not record.available:
            raise CapabilityUnavailableError(key, record)
        return record

    def public_snapshot(self) -> tuple[dict, ...]:
        return tuple(
            record.model_dump(mode="json")
            | {"available": record.available}
            for record in sorted(self._records.values(), key=lambda item: item.key)
        )


class CapabilityUnavailableError(RuntimeError):
    def __init__(self, key: str, record: CapabilityRecord) -> None:
        self.key = key
        self.record = record
        super().__init__(f"capability {key!r} is {record.status.value}")


def derive_provider_capability(
    *,
    key: str,
    provider: str,
    implemented: bool,
    configured: bool = True,
    owner_enabled: bool,
    feature_flag_enabled: bool,
    credentials_present: bool,
    approval_granted: bool,
    user_eligible: bool = True,
    healthy: bool = True,
    degraded: bool = False,
) -> CapabilityRecord:
    """Derive status in a stable precedence order from server-known facts."""

    if not implemented:
        status, reason = CapabilityStatus.PROVIDER_UNSUPPORTED, "Provider/API capability is not implemented."
    elif not configured:
        status, reason = CapabilityStatus.NOT_CONFIGURED, "Provider capability has not been configured."
    elif not owner_enabled:
        status, reason = CapabilityStatus.DISABLED, "Disabled by Owner configuration."
    elif not feature_flag_enabled:
        status, reason = CapabilityStatus.FEATURE_FLAGGED, "Feature flag is not enabled."
    elif not credentials_present:
        status, reason = CapabilityStatus.CREDENTIALS_MISSING, "Required server credentials are missing."
    elif not approval_granted:
        status, reason = CapabilityStatus.PLATFORM_APPROVAL_PENDING, "External platform approval is pending."
    elif not user_eligible:
        status, reason = CapabilityStatus.USER_INELIGIBLE, "This account is not eligible for the capability."
    elif not healthy:
        status, reason = CapabilityStatus.TEMPORARILY_UNAVAILABLE, "Provider is temporarily unavailable."
    elif degraded:
        status, reason = CapabilityStatus.DEGRADED, "Capability is available with degraded service."
    else:
        status, reason = CapabilityStatus.ENABLED, "Capability is available."
    return CapabilityRecord(
        key=key,
        provider=provider,
        status=status,
        reason=reason,
        owner_enabled=owner_enabled,
    )
