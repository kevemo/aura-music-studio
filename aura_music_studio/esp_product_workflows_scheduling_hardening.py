from __future__ import annotations

"""Scheduling guards for Chat 9 announcements.

The base workflow intentionally keeps announcement persistence small and additive. This
module closes the high-impact scheduling edge case at the service boundary so a row marked
``scheduled`` can never become immediately visible because ``publish_at`` was omitted.
It also normalises accepted timestamps to UTC before the existing SQL visibility comparison.
"""

from datetime import datetime, timezone

from .esp_product_workflows import Chat9WorkflowStore


def _utc_timestamp(value: str, field_name: str) -> tuple[str, datetime]:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    utc_value = parsed.astimezone(timezone.utc)
    return utc_value.isoformat(), utc_value


def _install_guard() -> None:
    current = Chat9WorkflowStore.create_announcement
    if getattr(current, "_chat9_scheduling_hardened", False):
        return

    original = current

    def create_announcement(self, payload, *, actor_user_id: str):
        now = datetime.now(timezone.utc)
        publish_time: datetime | None = None

        if payload.status == "scheduled":
            if not (payload.publish_at or "").strip():
                raise ValueError("publish_at is required for scheduled announcements")
            payload.publish_at, publish_time = _utc_timestamp(payload.publish_at, "publish_at")
            if publish_time <= now:
                raise ValueError("publish_at must be in the future for scheduled announcements")
        elif payload.publish_at:
            payload.publish_at, publish_time = _utc_timestamp(payload.publish_at, "publish_at")
            if payload.status == "published" and publish_time > now:
                raise ValueError("published announcements cannot have a future publish_at; use scheduled")

        if payload.expires_at:
            payload.expires_at, expires_time = _utc_timestamp(payload.expires_at, "expires_at")
            effective_publish = publish_time
            if effective_publish is None and payload.status == "published":
                effective_publish = now
            if effective_publish is not None and expires_time <= effective_publish:
                raise ValueError("expires_at must be later than publish_at")

        return original(self, payload, actor_user_id=actor_user_id)

    create_announcement._chat9_scheduling_hardened = True  # type: ignore[attr-defined]
    Chat9WorkflowStore.create_announcement = create_announcement


_install_guard()


__all__ = ["_utc_timestamp"]
