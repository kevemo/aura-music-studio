from __future__ import annotations

"""Fail-closed scheduling rules for Chat 9 announcements.

The base workflow already requires explicit confirmation for scheduled/published
announcements. This layer closes the remaining scheduling ambiguity by requiring a
real, timezone-aware publication timestamp for scheduled records and by preventing
an expiry timestamp from preceding (or equalling) publication.
"""

from datetime import datetime

from . import esp_product_workflows as base

_original_create_announcement = base.Chat9WorkflowStore.create_announcement


def _parse_aware_timestamp(value: str | None, *, field: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError(f"{field} is required for scheduled announcements")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit timezone offset")
    return parsed


def _secured_create_announcement(
    self: base.Chat9WorkflowStore,
    payload: base.AnnouncementCreate,
    *,
    actor_user_id: str,
) -> dict:
    if payload.status == "scheduled":
        publish_at = _parse_aware_timestamp(payload.publish_at, field="publish_at")
        if payload.expires_at:
            expires_at = _parse_aware_timestamp(payload.expires_at, field="expires_at")
            if expires_at <= publish_at:
                raise ValueError("expires_at must be later than publish_at")
    return _original_create_announcement(self, payload, actor_user_id=actor_user_id)


if not getattr(base.Chat9WorkflowStore.create_announcement, "_chat9_schedule_guard", False):
    _secured_create_announcement._chat9_schedule_guard = True  # type: ignore[attr-defined]
    base.Chat9WorkflowStore.create_announcement = _secured_create_announcement  # type: ignore[method-assign]


__all__ = []
