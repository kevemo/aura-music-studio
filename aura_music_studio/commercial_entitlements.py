from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .accounts import AccountStore
from .plans import IMAGE_POSTER_CREATE, IMAGE_POSTER_DOWNLOAD, MUSIC_VIDEO_DOWNLOAD

IMAGE_POSTER_GENERATION_EVENT = "image_poster_generation"


def _utc_day_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _usage_count(user_id: str, event_type: str, *, since: str) -> int:
    store = AccountStore()
    with sqlite3.connect(store.db_path) as con:
        row = con.execute(
            "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND event_type=? AND occurred_at>=?",
            (user_id, event_type, since),
        ).fetchone()
    return int(row[0] if row else 0)


def image_poster_usage(member) -> dict:
    plan = member.plan
    limit = plan.image_poster_creations_per_day
    used = _usage_count(member.user_id, IMAGE_POSTER_GENERATION_EVENT, since=_utc_day_start())
    remaining = None if limit is None else max(0, int(limit) - used)
    return {
        "plan": plan.id,
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "unlimited": limit is None,
        "timezone": "UTC",
    }


def require_image_poster_generation(member) -> dict:
    if not member.plan.has(IMAGE_POSTER_CREATE):
        raise PermissionError("Image and poster creation is not included in this membership tier")
    usage = image_poster_usage(member)
    if usage["limit"] is not None and usage["used"] >= usage["limit"]:
        raise PermissionError(
            f"Daily image/poster creation allowance reached ({usage['limit']} per day on this plan)"
        )
    return usage


def record_image_poster_generation(member, *, project_id: str, directive_id: str) -> dict:
    AccountStore().record_usage(
        member.user_id,
        IMAGE_POSTER_GENERATION_EVENT,
        project_id=project_id,
        metadata_json=json.dumps(
            {"category": "image_poster", "directive_id": directive_id, "plan": member.plan.id},
            ensure_ascii=False,
        ),
    )
    return image_poster_usage(member)


def can_download_media(member, kind: str) -> bool:
    normalized = str(kind or "").strip().lower()
    if normalized == "image":
        return member.plan.has(IMAGE_POSTER_DOWNLOAD)
    if normalized in {"audio", "music", "video"}:
        return member.plan.has(MUSIC_VIDEO_DOWNLOAD)
    return False


def require_media_download(member, kind: str) -> None:
    if can_download_media(member, kind):
        return
    normalized = str(kind or "").strip().lower()
    if normalized in {"audio", "music", "video"}:
        raise PermissionError("Music and video downloads are available from the £5.99 Tier 2 plan and above")
    if normalized == "image":
        raise PermissionError("Image/poster download is not included in this membership tier")
    raise PermissionError("This media type is not downloadable")


__all__ = [
    "IMAGE_POSTER_GENERATION_EVENT",
    "can_download_media",
    "image_poster_usage",
    "record_image_poster_generation",
    "require_image_poster_generation",
    "require_media_download",
]
