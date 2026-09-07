from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .aura_notifications import notification_store
from .owner_identity import owner_session_authorized
from . import shared_sky_live_community as live

router = APIRouter(tags=["Shared Sky Upcoming LIVE Events"])

PUBLICATION_VISIBILITY = {"public", "unlisted", "followers", "members", "restricted"}
LIVE_EVENT_CATEGORIES = {
    "music", "gaming", "video-film", "art-image", "education", "talk-community",
    "battles", "events", "other",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return []
        return list(decoded) if isinstance(decoded, list) else []
    return []


@dataclass(frozen=True)
class EventAccess:
    allowed: bool
    discoverable: bool
    reason: str


class EventPublicationRequest(BaseModel):
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="events", min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    language: str = Field(default="en", min_length=1, max_length=20)
    visibility: Literal["public", "unlisted", "followers", "members", "restricted"] = "public"
    suitability: str = Field(default="general", min_length=1, max_length=80)
    thumbnail_url: str = Field(default="", max_length=1200)
    expected_version: int | None = Field(default=None, ge=1)


class ReminderPreferenceRequest(BaseModel):
    enabled: bool = True
    lead_minutes: int = Field(default=15, ge=1, le=10080)


class LiveEventsStore:
    def __init__(self, community_store: Any = live.community, notifier: Any = notification_store):
        self.community = community_store
        self.db_path = community_store.db_path
        self.notifier = notifier
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_schedule_publications (
                    schedule_id TEXT PRIMARY KEY,
                    creator_user_id TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'events',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    language TEXT NOT NULL DEFAULT 'en',
                    visibility TEXT NOT NULL DEFAULT 'public',
                    suitability TEXT NOT NULL DEFAULT 'general',
                    thumbnail_url TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
                    published_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES shared_sky_schedules(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_schedule_publications_creator
                    ON shared_sky_schedule_publications(creator_user_id,updated_at DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_schedule_reminders (
                    schedule_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    lead_seconds INTEGER NOT NULL DEFAULT 900 CHECK(lead_seconds BETWEEN 60 AND 604800),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(schedule_id,user_id),
                    FOREIGN KEY(schedule_id) REFERENCES shared_sky_schedules(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_schedule_reminders_user
                    ON shared_sky_schedule_reminders(user_id,updated_at DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_schedule_reminder_emissions (
                    schedule_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    schedule_start_at TEXT NOT NULL,
                    lead_seconds INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    notification_id TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    claimed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(schedule_id,user_id,schedule_start_at,lead_seconds),
                    FOREIGN KEY(schedule_id) REFERENCES shared_sky_schedules(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_schedule_reminder_emissions_state
                    ON shared_sky_schedule_reminder_emissions(state,updated_at);
                """
            )

    def _schedule(self, schedule_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                """SELECT s.id,s.user_id,s.title,s.start_at,s.mode,s.state,s.created_at,s.updated_at,
                          u.display_name AS creator_display_name
                   FROM shared_sky_schedules s JOIN users u ON u.id=s.user_id WHERE s.id=?""",
                (schedule_id,),
            ).fetchone()
        if not row:
            raise KeyError(schedule_id)
        return dict(row)

    def _publication(self, schedule_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_schedule_publications WHERE schedule_id=?",
                (schedule_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = _json_list(item.pop("tags_json", "[]"))
        return item

    def access(self, schedule_id: str, viewer_user_id: str | None, *, direct: bool = False, owner: bool = False) -> EventAccess:
        schedule = self._schedule(schedule_id)
        publication = self._publication(schedule_id)
        if not publication or schedule["state"] != "scheduled":
            return EventAccess(False, False, "not_published")
        creator = str(schedule["user_id"])
        if owner or viewer_user_id == creator:
            return EventAccess(True, publication["visibility"] != "unlisted", "owner_or_creator")
        if self.community.is_blocked(creator, viewer_user_id):
            return EventAccess(False, False, "creator_blocked")
        visibility = str(publication["visibility"])
        if visibility == "public":
            return EventAccess(True, True, "public")
        if visibility == "unlisted":
            return EventAccess(bool(direct), False, "unlisted")
        if visibility == "followers":
            allowed = self.community.is_following(viewer_user_id, creator)
            return EventAccess(allowed, allowed, "followers_only")
        if visibility == "members":
            allowed = viewer_user_id is not None
            return EventAccess(allowed, allowed, "members_only")
        return EventAccess(False, False, "suitability_confirmation_required")

    def publish(self, schedule_id: str, actor_user_id: str, body: EventPublicationRequest) -> dict[str, Any]:
        schedule = self._schedule(schedule_id)
        if str(schedule["user_id"]) != actor_user_id:
            raise PermissionError("Only the scheduled LIVE creator can publish this event")
        if schedule["state"] != "scheduled":
            raise RuntimeError("Only scheduled Shared Sky events can be published")
        start = _parse(schedule["start_at"])
        if start is None:
            raise ValueError("Scheduled LIVE start_at must be timezone-aware ISO 8601 before publication")
        category = _clean(body.category, 80).lower()
        if category not in LIVE_EVENT_CATEGORIES:
            category = "other"
        tags: list[str] = []
        seen: set[str] = set()
        for raw in body.tags:
            tag = _clean(raw, 40).lower()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
        language = _clean(body.language, 20).lower() or "en"
        thumbnail = _clean(body.thumbnail_url, 1200)
        if thumbnail:
            parsed = urlparse(thumbnail)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Event thumbnail URL must use HTTP or HTTPS")
        now = _iso()
        with self._connect() as con:
            existing = con.execute(
                "SELECT version,published_at FROM shared_sky_schedule_publications WHERE schedule_id=?",
                (schedule_id,),
            ).fetchone()
            if existing and body.expected_version is not None and int(existing["version"]) != body.expected_version:
                raise RuntimeError("Event publication changed; refresh before editing")
            if not existing and body.expected_version not in {None, 1}:
                raise RuntimeError("Event publication does not exist at the expected version")
            version = int(existing["version"]) + 1 if existing else 1
            published_at = str(existing["published_at"]) if existing else now
            con.execute(
                """INSERT INTO shared_sky_schedule_publications
                   (schedule_id,creator_user_id,description,category,tags_json,language,visibility,
                    suitability,thumbnail_url,version,published_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(schedule_id) DO UPDATE SET
                    description=excluded.description,category=excluded.category,tags_json=excluded.tags_json,
                    language=excluded.language,visibility=excluded.visibility,suitability=excluded.suitability,
                    thumbnail_url=excluded.thumbnail_url,version=excluded.version,updated_at=excluded.updated_at""",
                (
                    schedule_id, actor_user_id, _clean(body.description, 2000), category,
                    json.dumps(tags, separators=(",", ":")), language, body.visibility,
                    _clean(body.suitability, 80).lower() or "general", thumbnail, version,
                    published_at, now,
                ),
            )
        return self.event(schedule_id, actor_user_id, direct=True, owner=False)

    def unpublish(self, schedule_id: str, actor_user_id: str, *, owner: bool = False) -> bool:
        schedule = self._schedule(schedule_id)
        if not owner and str(schedule["user_id"]) != actor_user_id:
            raise PermissionError("Only the scheduled LIVE creator can unpublish this event")
        with self._connect() as con:
            con.execute("DELETE FROM shared_sky_schedule_reminders WHERE schedule_id=?", (schedule_id,))
            con.execute("DELETE FROM shared_sky_schedule_reminder_emissions WHERE schedule_id=?", (schedule_id,))
            cur = con.execute("DELETE FROM shared_sky_schedule_publications WHERE schedule_id=?", (schedule_id,))
        return cur.rowcount > 0

    def _public(self, schedule: dict[str, Any], publication: dict[str, Any], viewer_user_id: str | None) -> dict[str, Any]:
        start = _parse(schedule["start_at"])
        return {
            "schedule_id": schedule["id"],
            "creator_user_id": schedule["user_id"],
            "creator_display_name": schedule["creator_display_name"],
            "title": schedule["title"],
            "description": publication["description"],
            "category": publication["category"],
            "tags": publication["tags"],
            "language": publication["language"],
            "visibility": publication["visibility"],
            "suitability": publication["suitability"],
            "thumbnail_url": publication["thumbnail_url"],
            "start_at": start.isoformat() if start else None,
            "mode": schedule["mode"],
            "version": int(publication["version"]),
            "following": self.community.is_following(viewer_user_id, str(schedule["user_id"])),
            "reminder": self.reminder(schedule["id"], viewer_user_id) if viewer_user_id else None,
        }

    def event(self, schedule_id: str, viewer_user_id: str | None, *, direct: bool, owner: bool = False) -> dict[str, Any]:
        decision = self.access(schedule_id, viewer_user_id, direct=direct, owner=owner)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        schedule = self._schedule(schedule_id)
        publication = self._publication(schedule_id)
        if not publication:
            raise KeyError(schedule_id)
        return self._public(schedule, publication, viewer_user_id)

    def list_events(
        self,
        viewer_user_id: str | None,
        *,
        q: str = "",
        category: str | None = None,
        language: str | None = None,
        following_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        owner: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT s.id,s.user_id,s.title,s.start_at,s.mode,s.state,s.created_at,s.updated_at,
                          u.display_name AS creator_display_name,p.*
                   FROM shared_sky_schedules s
                   JOIN shared_sky_schedule_publications p ON p.schedule_id=s.id
                   JOIN users u ON u.id=s.user_id
                   WHERE s.state='scheduled' ORDER BY s.start_at ASC LIMIT 500"""
            ).fetchall()
        now = _now()
        query = q.strip().casefold()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            start = _parse(item["start_at"])
            if start is None or start <= now:
                continue
            decision = self.access(str(item["id"]), viewer_user_id, direct=False, owner=owner)
            if not decision.allowed or not decision.discoverable:
                continue
            tags = _json_list(item.get("tags_json"))
            if following_only and not self.community.is_following(viewer_user_id, str(item["user_id"])):
                continue
            if category and category not in {"all", "recommended"} and str(item["category"]) != category:
                continue
            if language and str(item["language"]).lower() != language.lower():
                continue
            if query and query not in " ".join(
                [str(item["title"]), str(item["creator_display_name"]), str(item["category"]), *map(str, tags)]
            ).casefold():
                continue
            publication = {
                "description": item["description"], "category": item["category"], "tags": tags,
                "language": item["language"], "visibility": item["visibility"],
                "suitability": item["suitability"], "thumbnail_url": item["thumbnail_url"],
                "version": item["version"],
            }
            results.append(self._public(item, publication, viewer_user_id))
        start_offset = max(0, offset)
        return results[start_offset:start_offset + max(1, min(limit, 100))]

    def reminder(self, schedule_id: str, user_id: str | None) -> dict[str, Any] | None:
        if not user_id:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT lead_seconds,enabled,updated_at FROM shared_sky_schedule_reminders WHERE schedule_id=? AND user_id=?",
                (schedule_id, user_id),
            ).fetchone()
        if not row:
            return {"enabled": False, "lead_minutes": 15}
        return {
            "enabled": bool(row["enabled"]),
            "lead_minutes": int(row["lead_seconds"]) // 60,
            "updated_at": row["updated_at"],
        }

    def set_reminder(self, schedule_id: str, user_id: str, body: ReminderPreferenceRequest) -> dict[str, Any]:
        decision = self.access(schedule_id, user_id, direct=True)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        schedule = self._schedule(schedule_id)
        start = _parse(schedule["start_at"])
        if start is None or start <= _now():
            raise RuntimeError("This scheduled LIVE can no longer accept reminders")
        lead_seconds = int(body.lead_minutes) * 60
        now = _iso()
        with self._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_schedule_reminders
                   (schedule_id,user_id,lead_seconds,enabled,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(schedule_id,user_id) DO UPDATE SET
                   lead_seconds=excluded.lead_seconds,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (schedule_id, user_id, lead_seconds, int(body.enabled), now, now),
            )
        return self.reminder(schedule_id, user_id) or {"enabled": False, "lead_minutes": body.lead_minutes}

    def _claim_emission(self, schedule_id: str, user_id: str, start_at: str, lead_seconds: int, now: datetime) -> bool:
        now_iso = _iso(now)
        stale_before = _iso(now - timedelta(minutes=5))
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT state,claimed_at FROM shared_sky_schedule_reminder_emissions
                   WHERE schedule_id=? AND user_id=? AND schedule_start_at=? AND lead_seconds=?""",
                (schedule_id, user_id, start_at, lead_seconds),
            ).fetchone()
            if row and row["state"] == "sent":
                return False
            if row and row["state"] == "processing" and row["claimed_at"] and str(row["claimed_at"]) > stale_before:
                return False
            if row:
                con.execute(
                    """UPDATE shared_sky_schedule_reminder_emissions
                       SET state='processing',attempts=attempts+1,claimed_at=?,updated_at=?
                       WHERE schedule_id=? AND user_id=? AND schedule_start_at=? AND lead_seconds=?""",
                    (now_iso, now_iso, schedule_id, user_id, start_at, lead_seconds),
                )
            else:
                con.execute(
                    """INSERT INTO shared_sky_schedule_reminder_emissions
                       (schedule_id,user_id,schedule_start_at,lead_seconds,state,attempts,claimed_at,created_at,updated_at)
                       VALUES(?,?,?,?,'processing',1,?,?,?)""",
                    (schedule_id, user_id, start_at, lead_seconds, now_iso, now_iso, now_iso),
                )
        return True

    def _finish_emission(
        self,
        schedule_id: str,
        user_id: str,
        start_at: str,
        lead_seconds: int,
        *,
        notification_id: str | None,
        error: str = "",
    ) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE shared_sky_schedule_reminder_emissions
                   SET state=?,notification_id=?,last_error=?,updated_at=?
                   WHERE schedule_id=? AND user_id=? AND schedule_start_at=? AND lead_seconds=?""",
                (
                    "sent" if notification_id else "failed", notification_id, _clean(error, 500), _iso(),
                    schedule_id, user_id, start_at, lead_seconds,
                ),
            )

    def emit_due_reminders(self, *, now: datetime | None = None, limit: int = 500) -> dict[str, int]:
        current = (now or _now()).astimezone(timezone.utc)
        with self._connect() as con:
            rows = con.execute(
                """SELECT r.schedule_id,r.user_id,r.lead_seconds,s.title,s.start_at,s.user_id AS creator_user_id,
                          u.display_name AS creator_display_name
                   FROM shared_sky_schedule_reminders r
                   JOIN shared_sky_schedules s ON s.id=r.schedule_id
                   JOIN shared_sky_schedule_publications p ON p.schedule_id=s.id
                   JOIN users u ON u.id=s.user_id
                   WHERE r.enabled=1 AND s.state='scheduled'
                   ORDER BY s.start_at ASC LIMIT ?""",
                (max(1, min(int(limit), 2000)),),
            ).fetchall()
        checked = sent = skipped = failed = 0
        for row in rows:
            checked += 1
            schedule_id = str(row["schedule_id"])
            user_id = str(row["user_id"])
            start = _parse(row["start_at"])
            lead_seconds = int(row["lead_seconds"])
            if start is None or start < current or start > current + timedelta(seconds=lead_seconds):
                skipped += 1
                continue
            decision = self.access(schedule_id, user_id, direct=True)
            if not decision.allowed:
                skipped += 1
                continue
            start_key = start.isoformat()
            if not self._claim_emission(schedule_id, user_id, start_key, lead_seconds, current):
                skipped += 1
                continue
            try:
                minutes = max(1, int((start - current).total_seconds() // 60))
                notice = self.notifier.create(
                    user_id,
                    kind="shared_sky_schedule_reminder",
                    title=f"{row['creator_display_name']} goes LIVE soon",
                    body=f"{row['title']} starts in about {minutes} minute{'s' if minutes != 1 else ''}.",
                    resource_kind="shared_sky_schedule",
                    resource_id=schedule_id,
                )
                notification_id = str((notice or {}).get("id") or "")
                if not notification_id:
                    raise RuntimeError("Notification store did not return an ID")
                self._finish_emission(
                    schedule_id, user_id, start_key, lead_seconds, notification_id=notification_id
                )
                sent += 1
            except Exception as exc:
                self._finish_emission(
                    schedule_id, user_id, start_key, lead_seconds, notification_id=None, error=str(exc)
                )
                failed += 1
        return {"checked": checked, "sent": sent, "skipped": skipped, "failed": failed}


events_store = LiveEventsStore()


def _member(request: Request):
    return live.require_member(request)


def _optional_user(request: Request) -> str | None:
    member = live.optional_member(request)
    return member.user_id if member else None


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Sky scheduled LIVE not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "Shared Sky scheduled LIVE operation failed")


@router.get("/shared-sky/live/api/events")
def upcoming_live_events(
    request: Request,
    q: str = "",
    category: str | None = None,
    language: str | None = None,
    following_only: bool = False,
    limit: int = 50,
    offset: int = 0,
):
    user_id = _optional_user(request)
    return {
        "events": events_store.list_events(
            user_id, q=q, category=category, language=language, following_only=following_only,
            limit=limit, offset=offset, owner=owner_session_authorized(request),
        )
    }


@router.get("/shared-sky/live/api/events/{schedule_id}")
def upcoming_live_event(schedule_id: str, request: Request):
    user_id = _optional_user(request)
    try:
        return {
            "event": events_store.event(
                schedule_id, user_id, direct=True, owner=owner_session_authorized(request)
            )
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/shared-sky/live/api/schedules/{schedule_id}/publication")
def publish_live_event(schedule_id: str, body: EventPublicationRequest, request: Request):
    member = _member(request)
    try:
        return {"event": events_store.publish(schedule_id, member.user_id, body)}
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/shared-sky/live/api/schedules/{schedule_id}/publication")
def unpublish_live_event(schedule_id: str, request: Request):
    member = live.optional_member(request)
    owner = owner_session_authorized(request)
    if not owner and member is None:
        raise HTTPException(401, "Creator or owner authentication required")
    try:
        return {
            "unpublished": events_store.unpublish(
                schedule_id, member.user_id if member else "owner", owner=owner
            )
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/shared-sky/live/api/events/{schedule_id}/reminder")
def set_live_event_reminder(schedule_id: str, body: ReminderPreferenceRequest, request: Request):
    member = _member(request)
    try:
        return {"reminder": events_store.set_reminder(schedule_id, member.user_id, body)}
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/shared-sky/live/api/events/{schedule_id}/reminder")
def get_live_event_reminder(schedule_id: str, request: Request):
    member = _member(request)
    try:
        decision = events_store.access(schedule_id, member.user_id, direct=True)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return {"reminder": events_store.reminder(schedule_id, member.user_id)}
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/owner/shared-sky/live/api/reminders/emit-due")
def emit_due_live_event_reminders(request: Request):
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")
    return {
        "result": events_store.emit_due_reminders(),
        "runtime": "server_hook_manual_or_worker_invocation_required",
    }


__all__ = [
    "router", "LiveEventsStore", "EventPublicationRequest", "ReminderPreferenceRequest",
    "events_store", "EventAccess",
]
