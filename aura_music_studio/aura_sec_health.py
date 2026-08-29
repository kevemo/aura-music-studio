from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


DEFAULT_HEARTBEAT_HEALTHY_SECONDS = 180
DEFAULT_HEARTBEAT_STALE_SECONDS = 600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DeviceHealth:
    device_id: str
    status: str
    protection_state: str
    health: str
    reason: str
    last_seen_at: str | None
    heartbeat_age_seconds: int | None
    agent_version: str | None
    policy_version: str | None


def evaluate_device_health(
    device: dict,
    *,
    now: datetime | None = None,
    healthy_after_seconds: int = DEFAULT_HEARTBEAT_HEALTHY_SECONDS,
    stale_after_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
) -> DeviceHealth:
    """Derive truthful device health from verified persisted state and freshness.

    A persisted `healthy` string is not enough. Once the last verified heartbeat becomes
    stale, the control plane must stop presenting the device as currently healthy.
    """
    current = (now or _now()).astimezone(timezone.utc)
    device_id = str(device.get("id") or "")
    status = str(device.get("status") or "unknown")
    protection_state = str(device.get("protection_state") or "unknown")
    last_seen = _parse(device.get("last_seen_at"))

    if status == "revoked" or device.get("revoked_at"):
        return DeviceHealth(
            device_id=device_id,
            status="revoked",
            protection_state="not_managed",
            health="not_managed",
            reason="Device enrolment has been revoked.",
            last_seen_at=device.get("last_seen_at"),
            heartbeat_age_seconds=None if not last_seen else max(0, int((current - last_seen).total_seconds())),
            agent_version=device.get("agent_version"),
            policy_version=device.get("last_policy_version"),
        )

    if not last_seen:
        return DeviceHealth(
            device_id=device_id,
            status=status,
            protection_state=protection_state,
            health="awaiting_verified_heartbeat",
            reason="No verified device heartbeat has been recorded yet.",
            last_seen_at=None,
            heartbeat_age_seconds=None,
            agent_version=device.get("agent_version"),
            policy_version=device.get("last_policy_version"),
        )

    age = max(0, int((current - last_seen).total_seconds()))
    if age > stale_after_seconds:
        return DeviceHealth(
            device_id=device_id,
            status=status,
            protection_state=protection_state,
            health="stale",
            reason="The most recent verified heartbeat is too old to claim current protection health.",
            last_seen_at=device.get("last_seen_at"),
            heartbeat_age_seconds=age,
            agent_version=device.get("agent_version"),
            policy_version=device.get("last_policy_version"),
        )

    if protection_state == "healthy" and age <= healthy_after_seconds:
        health = "healthy"
        reason = "Recent verified heartbeat reports healthy protection state."
    elif protection_state == "healthy":
        health = "recent_but_aging"
        reason = "Heartbeat is still recent, but outside the strict healthy freshness window."
    elif protection_state in {"degraded", "attention_required", "isolated"}:
        health = protection_state
        reason = f"Recent verified heartbeat reports {protection_state.replace('_', ' ')}."
    elif protection_state == "updating":
        health = "updating"
        reason = "Recent verified heartbeat reports an update in progress."
    else:
        health = "unknown"
        reason = "Verified heartbeat contains an unrecognised or incomplete protection state."

    return DeviceHealth(
        device_id=device_id,
        status=status,
        protection_state=protection_state,
        health=health,
        reason=reason,
        last_seen_at=device.get("last_seen_at"),
        heartbeat_age_seconds=age,
        agent_version=device.get("agent_version"),
        policy_version=device.get("last_policy_version"),
    )


def summarize_security_health(devices: Iterable[dict], *, now: datetime | None = None) -> dict:
    evaluated = [evaluate_device_health(device, now=now) for device in devices]
    managed = [item for item in evaluated if item.health != "not_managed"]
    healthy = [item for item in managed if item.health == "healthy"]
    needs_attention = [
        item
        for item in managed
        if item.health
        in {
            "stale",
            "degraded",
            "attention_required",
            "isolated",
            "unknown",
            "awaiting_verified_heartbeat",
        }
    ]
    overall = "not_enrolled"
    if managed:
        overall = "healthy" if len(healthy) == len(managed) else "attention_required"

    return {
        "overall": overall,
        "total_devices": len(evaluated),
        "managed_devices": len(managed),
        "healthy_devices": len(healthy),
        "attention_devices": len(needs_attention),
        "devices": [item.__dict__.copy() for item in evaluated],
        "truth": (
            "A device is counted healthy only when its most recent verified heartbeat is inside the strict freshness window."
        ),
    }


__all__ = [
    "DEFAULT_HEARTBEAT_HEALTHY_SECONDS",
    "DEFAULT_HEARTBEAT_STALE_SECONDS",
    "DeviceHealth",
    "evaluate_device_health",
    "summarize_security_health",
]
