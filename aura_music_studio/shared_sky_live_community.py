from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .aura_notifications import notification_store
from .membership import MembershipService
from .owner_identity import owner_session_authorized
from .shared_sky_streaming_studios import PLATFORM_REGISTRY, shared_sky

router = APIRouter(tags=["Shared Sky Live Now & Community"])

LIVE_VISIBILITIES = {"public", "unlisted", "followers", "members", "restricted"}
LIVE_CATEGORIES = {
    "recommended", "music", "gaming", "video-film", "art-image", "education",
    "talk-community", "esp-creators", "battles", "events", "other",
}
REACTION_TYPES = {"like", "star", "spark", "applause", "heart", "wow"}
DURABLE_EVENT_TYPES = {
    "chat.message", "chat.deleted", "chat.pinned", "chat.notice", "chat.settings",
    "poll.created", "poll.voted", "poll.closed", "poll.cancelled",
    "qa.submitted", "qa.approved", "qa.rejected", "qa.selected", "qa.answered", "qa.removed",
    "moderation.action", "live.directory", "live.ended",
}
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_RE = re.compile(r"https?://[^\s<>{}\[\]\"']+", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _json(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _clean_text(value: str, limit: int) -> str:
    clean = _TEXT_CONTROL.sub("", str(value or "")).strip()
    return clean[:limit]


def _safe_links(text: str) -> list[str]:
    links: list[str] = []
    for match in _URL_RE.findall(text):
        try:
            parsed = urlparse(match)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                links.append(match[:1000])
        except Exception:
            continue
    return links[:8]


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("lss_session")


_accounts = AccountStore()
_memberships = MembershipService(_accounts)


def optional_member(request: Request):
    existing = getattr(request.state, "member", None)
    if existing is not None:
        return existing
    token = _session_token(request)
    if not token:
        return None
    try:
        return _memberships.from_session(token, require_active=True)
    except PermissionError:
        return None


def require_member(request: Request):
    member = optional_member(request)
    if member is None:
        raise HTTPException(401, "Sign in required for this LIVE community action")
    return member


class PlaybackAdapter(Protocol):
    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...
    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...


class GiftDisplayAdapter(Protocol):
    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...


class BattleDisplayAdapter(Protocol):
    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...


class ExternalChatAdapter(Protocol):
    def capability(self, broadcast_id: str, platform_id: str, viewer_user_id: str | None) -> dict: ...


class UnavailablePlaybackAdapter:
    """Fail-closed compatibility adapter until Chat 2 registers canonical playback."""

    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {
            "available": False,
            "state": "unavailable",
            "reason": "playback_contract_unavailable",
            "broadcast_id": broadcast_id,
            "manifest_url": None,
            "token_expires_at": None,
            "renditions": [],
            "captions": [],
            "dvr": False,
        }

    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {
            "available": False,
            "state": "unavailable",
            "reason": "replay_contract_unavailable",
            "broadcast_id": broadcast_id,
        }


class UnavailableGiftDisplayAdapter:
    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": False, "reason": "gift_contract_unavailable", "catalogue": [], "goal": None}


class UnavailableBattleDisplayAdapter:
    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": False, "reason": "battle_contract_unavailable", "battle": None}


class CapabilityOnlyExternalChatAdapter:
    def capability(self, broadcast_id: str, platform_id: str, viewer_user_id: str | None) -> dict:
        row = next((p for p in PLATFORM_REGISTRY if p["id"] == platform_id), None)
        capabilities = list((row or {}).get("capabilities") or [])
        supported = "chat" in capabilities or "chat_when_authorised" in capabilities
        return {
            "platform_id": platform_id,
            "authorised": False,
            "read_supported": False,
            "reply_supported": False,
            "moderate_supported": False,
            "provider_capability_declared": supported,
            "reason": "provider_authorisation_required" if supported else "provider_chat_unsupported",
        }


_playback_adapter: PlaybackAdapter = UnavailablePlaybackAdapter()
_gift_adapter: GiftDisplayAdapter = UnavailableGiftDisplayAdapter()
_battle_adapter: BattleDisplayAdapter = UnavailableBattleDisplayAdapter()
_external_chat_adapter: ExternalChatAdapter = CapabilityOnlyExternalChatAdapter()


def register_playback_adapter(adapter: PlaybackAdapter) -> None:
    global _playback_adapter
    _playback_adapter = adapter


def register_gift_display_adapter(adapter: GiftDisplayAdapter) -> None:
    global _gift_adapter
    _gift_adapter = adapter


def register_battle_display_adapter(adapter: BattleDisplayAdapter) -> None:
    global _battle_adapter
    _battle_adapter = adapter


def register_external_chat_adapter(adapter: ExternalChatAdapter) -> None:
    global _external_chat_adapter
    _external_chat_adapter = adapter


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    discoverable: bool
    reason: str


class LiveMetadataPatch(BaseModel):
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=20)
    language: str | None = Field(default=None, max_length=20)
    visibility: Literal["public", "unlisted", "followers", "members", "restricted"] | None = None
    suitability: Literal["general", "mature", "restricted"] | None = None
    thumbnail_url: str | None = Field(default=None, max_length=1200)
    captions_available: bool | None = None
    scheduled_event_id: str | None = Field(default=None, max_length=120)
    external_chat_links: dict[str, str] | None = None
    expected_version: int | None = Field(default=None, ge=1)


class PresenceJoinRequest(BaseModel):
    resume_token: str | None = Field(default=None, max_length=256)


class PresenceHeartbeatRequest(BaseModel):
    presence_id: str = Field(min_length=16, max_length=80)
    resume_token: str = Field(min_length=24, max_length=256)


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    reply_to_id: str | None = Field(default=None, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReactionRequest(BaseModel):
    reaction: Literal["like", "star", "spark", "applause", "heart", "wow"] = "like"
    amount: int = Field(default=1, ge=1, le=20)
    idempotency_key: str = Field(min_length=8, max_length=160)
    presence_token: str | None = Field(default=None, max_length=256)


class ShareRequest(BaseModel):
    action: Literal["copy_link", "share_sheet_open", "destination_open"]
    destination: str | None = Field(default=None, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=160)


class FollowRequest(BaseModel):
    following: bool
    notify_live: bool | None = None
    idempotency_key: str = Field(min_length=8, max_length=160)


class NotificationPreferenceRequest(BaseModel):
    notify_live: bool


class PollCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=240)
    options: list[str] = Field(min_length=2, max_length=8)
    multiple_choice: bool = False
    hide_until_voted: bool = False
    hide_until_ended: bool = False
    duration_seconds: int | None = Field(default=None, ge=15, le=86400)
    idempotency_key: str = Field(min_length=8, max_length=160)


class PollVoteRequest(BaseModel):
    option_ids: list[str] = Field(min_length=1, max_length=8)
    idempotency_key: str = Field(min_length=8, max_length=160)
    presence_token: str | None = Field(default=None, max_length=256)


class QARequest(BaseModel):
    question: str = Field(min_length=1, max_length=600)
    idempotency_key: str = Field(min_length=8, max_length=160)


class QAModerationRequest(BaseModel):
    action: Literal["approve", "reject", "select", "answered", "remove"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReportRequest(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    reason: str = Field(default="", max_length=1000)
    target_user_id: str | None = Field(default=None, max_length=80)
    message_id: str | None = Field(default=None, max_length=80)


class ModerationRequest(BaseModel):
    action: Literal[
        "pin_message", "unpin_message", "delete_message", "timeout_user", "remove_user",
        "block_user", "unblock_user", "set_slow_mode", "set_chat_enabled",
        "set_followers_only", "set_members_only",
    ]
    target_user_id: str | None = Field(default=None, max_length=80)
    message_id: str | None = Field(default=None, max_length=80)
    duration_seconds: int | None = Field(default=None, ge=1, le=604800)
    value: bool | int | None = None
    reason: str = Field(default="", max_length=500)
    expected_version: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=8, max_length=160)


class LiveCommunityStore:
    """Shared Sky viewer/community state on the canonical application database.

    The authoritative live session remains `shared_sky_broadcasts`; this store only indexes and
    enriches that state for viewers. It never creates/starts/stops transport sessions, debits
    Creation Coins, or computes Battle scores.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or shared_sky.db_path)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_live_directory (
                    broadcast_id TEXT PRIMARY KEY,
                    creator_user_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'talk-community',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    language TEXT NOT NULL DEFAULT 'en',
                    visibility TEXT NOT NULL DEFAULT 'public',
                    suitability TEXT NOT NULL DEFAULT 'general',
                    thumbnail_url TEXT NOT NULL DEFAULT '',
                    captions_available INTEGER NOT NULL DEFAULT 0,
                    scheduled_event_id TEXT,
                    external_chat_links_json TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 1,
                    indexed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_live_directory_discovery
                    ON shared_sky_live_directory(visibility,category,updated_at DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_follows (
                    follower_user_id TEXT NOT NULL,
                    creator_user_id TEXT NOT NULL,
                    notify_live INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(follower_user_id,creator_user_id),
                    FOREIGN KEY(follower_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_follows_creator
                    ON shared_sky_follows(creator_user_id,follower_user_id);

                CREATE TABLE IF NOT EXISTS shared_sky_blocks (
                    blocker_user_id TEXT NOT NULL,
                    blocked_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(blocker_user_id,blocked_user_id),
                    FOREIGN KEY(blocker_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(blocked_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_presence (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    viewer_key TEXT NOT NULL,
                    viewer_user_id TEXT,
                    token_hash TEXT NOT NULL UNIQUE,
                    joined_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    user_agent_hash TEXT NOT NULL DEFAULT '',
                    ip_hash TEXT NOT NULL DEFAULT '',
                    UNIQUE(broadcast_id,viewer_key),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(viewer_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_presence_live
                    ON shared_sky_presence(broadcast_id,expires_at);

                CREATE TABLE IF NOT EXISTS shared_sky_realtime_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    broadcast_id TEXT NOT NULL,
                    actor_user_id TEXT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    audience TEXT NOT NULL DEFAULT 'room',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    durable INTEGER NOT NULL DEFAULT 1,
                    moderation_state TEXT NOT NULL DEFAULT 'visible',
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_sky_events_idem
                    ON shared_sky_realtime_events(broadcast_id,event_type,idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_shared_sky_events_cursor
                    ON shared_sky_realtime_events(broadcast_id,seq);

                CREATE TABLE IF NOT EXISTS shared_sky_chat_settings (
                    broadcast_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    slow_seconds INTEGER NOT NULL DEFAULT 0,
                    followers_only INTEGER NOT NULL DEFAULT 0,
                    members_only INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS shared_sky_chat_messages (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    sender_user_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    reply_to_id TEXT,
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    edited_at TEXT,
                    deleted_at TEXT,
                    pinned_at TEXT,
                    moderation_state TEXT NOT NULL DEFAULT 'visible',
                    links_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(broadcast_id,sender_user_id,idempotency_key),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(sender_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_chat_history
                    ON shared_sky_chat_messages(broadcast_id,created_at,id);

                CREATE TABLE IF NOT EXISTS shared_sky_live_moderators (
                    broadcast_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    granted_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(broadcast_id,user_id),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS shared_sky_live_bans (
                    broadcast_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    expires_at TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    PRIMARY KEY(broadcast_id,user_id,action),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_engagement_receipts (
                    broadcast_id TEXT NOT NULL,
                    actor_key TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(broadcast_id,actor_key,action_type,idempotency_key),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS shared_sky_reaction_totals (
                    broadcast_id TEXT NOT NULL,
                    reaction_type TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(broadcast_id,reaction_type),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_polls (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    multiple_choice INTEGER NOT NULL DEFAULT 0,
                    hide_until_voted INTEGER NOT NULL DEFAULT 0,
                    hide_until_ended INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'live',
                    created_by TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT,
                    closed_at TEXT,
                    idempotency_key TEXT NOT NULL,
                    UNIQUE(broadcast_id,created_by,idempotency_key),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS shared_sky_poll_options (
                    id TEXT PRIMARY KEY,
                    poll_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    FOREIGN KEY(poll_id) REFERENCES shared_sky_polls(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS shared_sky_poll_votes (
                    poll_id TEXT NOT NULL,
                    voter_key TEXT NOT NULL,
                    option_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(poll_id,voter_key,option_id),
                    FOREIGN KEY(poll_id) REFERENCES shared_sky_polls(id) ON DELETE CASCADE,
                    FOREIGN KEY(option_id) REFERENCES shared_sky_poll_options(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_qa (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    asker_user_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    selected INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(broadcast_id,asker_user_id,idempotency_key),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(asker_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_reports (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    reporter_user_id TEXT,
                    target_user_id TEXT,
                    message_id TEXT,
                    category TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL DEFAULT 'submitted',
                    created_at TEXT NOT NULL,
                    audit_id TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_reports_live
                    ON shared_sky_reports(broadcast_id,created_at DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_moderation_actions (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    actor_user_id TEXT,
                    actor_kind TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_user_id TEXT,
                    message_id TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    correlation_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(broadcast_id,actor_kind,actor_user_id,idempotency_key),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_watch_history (
                    user_id TEXT NOT NULL,
                    broadcast_id TEXT NOT NULL,
                    viewed_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    replay_progress_seconds REAL,
                    PRIMARY KEY(user_id,broadcast_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_notification_emissions (
                    broadcast_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(broadcast_id,user_id,kind),
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_rate_limits (
                    actor_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY(actor_key,action,bucket)
                );
                """
            )

    def _broadcast(self, broadcast_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT b.*,u.display_name AS creator_display_name
                   FROM shared_sky_broadcasts b
                   JOIN users u ON u.id=b.user_id WHERE b.id=?""",
                (broadcast_id,),
            ).fetchone()
        if not row:
            raise KeyError(broadcast_id)
        return dict(row)

    def _directory_row(self, broadcast_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_live_directory WHERE broadcast_id=?", (broadcast_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = _json(item.pop("tags_json", "[]"), [])
        item["external_chat_links"] = _json(item.pop("external_chat_links_json", "{}"), {})
        item["captions_available"] = bool(item["captions_available"])
        return item

    def rate_limit(self, actor_key: str, action: str, *, limit: int, window_seconds: int) -> None:
        bucket = int(time.time()) // max(1, window_seconds)
        with self._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_rate_limits(actor_key,action,bucket,count) VALUES(?,?,?,1)
                   ON CONFLICT(actor_key,action,bucket) DO UPDATE SET count=count+1""",
                (actor_key[:200], action[:80], bucket),
            )
            count = int(con.execute(
                "SELECT count FROM shared_sky_rate_limits WHERE actor_key=? AND action=? AND bucket=?",
                (actor_key[:200], action[:80], bucket),
            ).fetchone()[0])
        if count > limit:
            raise PermissionError("Rate limit exceeded")

    def actor_key(self, request: Request, viewer_user_id: str | None = None) -> str:
        if viewer_user_id:
            return f"user:{viewer_user_id}"
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        host = forwarded or (request.client.host if request.client else "unknown")
        ua = request.headers.get("user-agent", "")
        digest = hashlib.sha256(f"{host}|{ua}".encode()).hexdigest()
        return f"anon:{digest}"

    def reconcile(self) -> dict:
        """Mirror canonical live sessions without making directory state authoritative."""
        now = _now()
        with self._connect() as con:
            live = con.execute("SELECT id,user_id FROM shared_sky_broadcasts WHERE state='live'").fetchall()
            live_ids = {str(row["id"]) for row in live}
            added = 0
            for row in live:
                existing = con.execute(
                    "SELECT 1 FROM shared_sky_live_directory WHERE broadcast_id=?", (row["id"],)
                ).fetchone()
                if not existing:
                    con.execute(
                        """INSERT INTO shared_sky_live_directory
                           (broadcast_id,creator_user_id,indexed_at,updated_at)
                           VALUES(?,?,?,?)""",
                        (row["id"], row["user_id"], now, now),
                    )
                    con.execute(
                        """INSERT OR IGNORE INTO shared_sky_chat_settings(broadcast_id,updated_at)
                           VALUES(?,?)""",
                        (row["id"], now),
                    )
                    added += 1
            rows = con.execute("SELECT broadcast_id FROM shared_sky_live_directory").fetchall()
            stale = [str(row["broadcast_id"]) for row in rows if str(row["broadcast_id"]) not in live_ids]
        for broadcast_id in live_ids:
            self._notify_followers_once(broadcast_id)
        return {"live": len(live_ids), "indexed_added": added, "stale_not_discoverable": len(stale)}

    def _notify_followers_once(self, broadcast_id: str) -> None:
        try:
            broadcast = self._broadcast(broadcast_id)
        except KeyError:
            return
        with self._connect() as con:
            followers = con.execute(
                """SELECT follower_user_id FROM shared_sky_follows
                   WHERE creator_user_id=? AND notify_live=1""",
                (broadcast["user_id"],),
            ).fetchall()
        for row in followers:
            user_id = str(row["follower_user_id"])
            with self._connect() as con:
                cur = con.execute(
                    """INSERT OR IGNORE INTO shared_sky_notification_emissions
                       (broadcast_id,user_id,kind,created_at) VALUES(?,?,?,?)""",
                    (broadcast_id, user_id, "creator_live", _now()),
                )
                created = cur.rowcount > 0
            if created:
                try:
                    notification_store.create(
                        user_id,
                        kind="shared_sky_live",
                        title=f"{broadcast['creator_display_name']} is LIVE",
                        body=broadcast["title"],
                        resource_kind="shared_sky_live",
                        resource_id=broadcast_id,
                    )
                except Exception:
                    pass

    def update_metadata(self, broadcast_id: str, actor_user_id: str, body: LiveMetadataPatch) -> dict:
        broadcast = self._broadcast(broadcast_id)
        if broadcast["user_id"] != actor_user_id:
            raise PermissionError("Only the LIVE creator can edit viewer metadata")
        self.reconcile()
        current = self._directory_row(broadcast_id)
        if not current:
            raise KeyError(broadcast_id)
        if body.expected_version is not None and int(current["version"]) != body.expected_version:
            raise RuntimeError("Live metadata changed; refresh before editing")
        category = (body.category or current["category"]).strip().lower()
        if category not in LIVE_CATEGORIES:
            category = "other"
        tags = current["tags"] if body.tags is None else [
            _clean_text(tag, 40).lower() for tag in body.tags if _clean_text(tag, 40)
        ][:20]
        language = _clean_text(body.language if body.language is not None else current["language"], 20).lower() or "en"
        visibility = body.visibility or current["visibility"]
        suitability = body.suitability or current["suitability"]
        thumbnail = _clean_text(body.thumbnail_url if body.thumbnail_url is not None else current["thumbnail_url"], 1200)
        if thumbnail and urlparse(thumbnail).scheme not in {"https", "http"}:
            raise ValueError("Thumbnail URL must use HTTP or HTTPS")
        links = current["external_chat_links"] if body.external_chat_links is None else {}
        if body.external_chat_links is not None:
            for platform_id, url in body.external_chat_links.items():
                clean_id = _clean_text(platform_id, 80).lower()
                parsed = urlparse(str(url).strip())
                if clean_id and parsed.scheme in {"http", "https"} and parsed.hostname:
                    links[clean_id] = str(url).strip()[:1200]
        captions = current["captions_available"] if body.captions_available is None else body.captions_available
        scheduled = current["scheduled_event_id"] if body.scheduled_event_id is None else _clean_text(body.scheduled_event_id, 120) or None
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE shared_sky_live_directory SET category=?,tags_json=?,language=?,visibility=?,
                   suitability=?,thumbnail_url=?,captions_available=?,scheduled_event_id=?,
                   external_chat_links_json=?,version=version+1,updated_at=? WHERE broadcast_id=?""",
                (category, json.dumps(tags), language, visibility, suitability, thumbnail, int(captions),
                 scheduled, json.dumps(links), now, broadcast_id),
            )
        self.emit(broadcast_id, actor_user_id, "live.directory", {"version": int(current["version"]) + 1})
        return self._directory_row(broadcast_id) or {}

    def is_following(self, viewer_user_id: str | None, creator_user_id: str) -> bool:
        if not viewer_user_id:
            return False
        with self._connect() as con:
            return bool(con.execute(
                "SELECT 1 FROM shared_sky_follows WHERE follower_user_id=? AND creator_user_id=?",
                (viewer_user_id, creator_user_id),
            ).fetchone())

    def is_blocked(self, creator_user_id: str, viewer_user_id: str | None) -> bool:
        if not viewer_user_id:
            return False
        with self._connect() as con:
            return bool(con.execute(
                "SELECT 1 FROM shared_sky_blocks WHERE blocker_user_id=? AND blocked_user_id=?",
                (creator_user_id, viewer_user_id),
            ).fetchone())

    def access(self, broadcast_id: str, viewer_user_id: str | None, *, direct: bool = False, owner: bool = False) -> AccessDecision:
        broadcast = self._broadcast(broadcast_id)
        meta = self._directory_row(broadcast_id)
        if not meta:
            return AccessDecision(False, False, "not_indexed")
        if owner or viewer_user_id == broadcast["user_id"]:
            return AccessDecision(True, False if meta["visibility"] == "unlisted" else True, "owner_or_creator")
        if broadcast["state"] not in {"live", "ended"}:
            return AccessDecision(False, False, "not_available")
        if self.is_blocked(broadcast["user_id"], viewer_user_id):
            return AccessDecision(False, False, "creator_blocked")
        visibility = meta["visibility"]
        if visibility == "public":
            return AccessDecision(True, broadcast["state"] == "live", "public")
        if visibility == "unlisted":
            return AccessDecision(bool(direct), False, "unlisted")
        if visibility == "followers":
            allowed = self.is_following(viewer_user_id, broadcast["user_id"])
            return AccessDecision(allowed, allowed and broadcast["state"] == "live", "followers_only")
        if visibility == "members":
            allowed = viewer_user_id is not None
            return AccessDecision(allowed, allowed and broadcast["state"] == "live", "members_only")
        return AccessDecision(False, False, "suitability_confirmation_required")

    def presence_count(self, broadcast_id: str) -> int:
        now = _now()
        with self._connect() as con:
            con.execute("DELETE FROM shared_sky_presence WHERE expires_at<=?", (now,))
            row = con.execute(
                "SELECT COUNT(*) AS n FROM shared_sky_presence WHERE broadcast_id=? AND expires_at>?",
                (broadcast_id, now),
            ).fetchone()
        return int(row["n"] or 0)

    def join_presence(self, broadcast_id: str, request: Request, viewer_user_id: str | None, resume_token: str | None = None) -> dict:
        decision = self.access(broadcast_id, viewer_user_id, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        broadcast = self._broadcast(broadcast_id)
        if broadcast["state"] != "live":
            raise RuntimeError("LIVE has ended")
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(seconds=45)).isoformat()
        token_hash = hashlib.sha256((resume_token or "").encode()).hexdigest() if resume_token else ""
        with self._connect() as con:
            row = None
            if resume_token:
                row = con.execute(
                    "SELECT * FROM shared_sky_presence WHERE broadcast_id=? AND token_hash=?",
                    (broadcast_id, token_hash),
                ).fetchone()
            if viewer_user_id:
                row = con.execute(
                    "SELECT * FROM shared_sky_presence WHERE broadcast_id=? AND viewer_key=?",
                    (broadcast_id, f"user:{viewer_user_id}"),
                ).fetchone() or row
            if row:
                presence_id = str(row["id"])
                if resume_token and token_hash == row["token_hash"]:
                    raw_token = resume_token
                else:
                    raw_token = secrets.token_urlsafe(32)
                    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                con.execute(
                    """UPDATE shared_sky_presence SET viewer_user_id=?,token_hash=?,last_seen_at=?,expires_at=?
                       WHERE id=?""",
                    (viewer_user_id, token_hash, now_dt.isoformat(), expires, presence_id),
                )
            else:
                presence_id = uuid4().hex
                raw_token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                viewer_key = f"user:{viewer_user_id}" if viewer_user_id else f"anon:{uuid4().hex}"
                ua_hash = hashlib.sha256(request.headers.get("user-agent", "").encode()).hexdigest()
                host = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip() or (request.client.host if request.client else "")
                ip_hash = hashlib.sha256(host.encode()).hexdigest()
                con.execute(
                    """INSERT INTO shared_sky_presence
                       (id,broadcast_id,viewer_key,viewer_user_id,token_hash,joined_at,last_seen_at,expires_at,user_agent_hash,ip_hash)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (presence_id, broadcast_id, viewer_key, viewer_user_id, token_hash,
                     now_dt.isoformat(), now_dt.isoformat(), expires, ua_hash, ip_hash),
                )
        self.watch(broadcast_id, viewer_user_id)
        return {
            "presence_id": presence_id,
            "resume_token": raw_token,
            "lease_seconds": 45,
            "heartbeat_seconds": 20,
            "viewer_count": self.presence_count(broadcast_id),
            "count_definition": "current unexpired unique Shared Sky presence leases",
        }

    def heartbeat(self, broadcast_id: str, presence_id: str, resume_token: str) -> dict:
        token_hash = hashlib.sha256(resume_token.encode()).hexdigest()
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(seconds=45)).isoformat()
        with self._connect() as con:
            cur = con.execute(
                """UPDATE shared_sky_presence SET last_seen_at=?,expires_at=?
                   WHERE id=? AND broadcast_id=? AND token_hash=?""",
                (now_dt.isoformat(), expires, presence_id, broadcast_id, token_hash),
            )
        if cur.rowcount < 1:
            raise PermissionError("Presence lease is invalid or expired")
        return {"ok": True, "viewer_count": self.presence_count(broadcast_id), "expires_at": expires}

    def leave_presence(self, broadcast_id: str, presence_id: str, resume_token: str) -> None:
        token_hash = hashlib.sha256(resume_token.encode()).hexdigest()
        with self._connect() as con:
            con.execute(
                "DELETE FROM shared_sky_presence WHERE id=? AND broadcast_id=? AND token_hash=?",
                (presence_id, broadcast_id, token_hash),
            )

    def presence_actor(self, broadcast_id: str, resume_token: str | None) -> str | None:
        if not resume_token:
            return None
        token_hash = hashlib.sha256(resume_token.encode()).hexdigest()
        with self._connect() as con:
            row = con.execute(
                """SELECT viewer_key FROM shared_sky_presence
                   WHERE broadcast_id=? AND token_hash=? AND expires_at>?""",
                (broadcast_id, token_hash, _now()),
            ).fetchone()
        return str(row["viewer_key"]) if row else None

    def emit(self, broadcast_id: str, actor_user_id: str | None, event_type: str, payload: dict, *, idempotency_key: str | None = None, durable: bool | None = None, audience: str = "room", moderation_state: str = "visible", correlation_id: str | None = None) -> dict:
        event_id = uuid4().hex
        occurred_at = _now()
        durable_value = event_type in DURABLE_EVENT_TYPES if durable is None else durable
        correlation = correlation_id or uuid4().hex
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO shared_sky_realtime_events
                       (event_id,broadcast_id,actor_user_id,event_type,occurred_at,correlation_id,
                        idempotency_key,audience,payload_json,durable,moderation_state)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, broadcast_id, actor_user_id, event_type, occurred_at, correlation,
                     idempotency_key, audience, json.dumps(payload, separators=(",", ":")),
                     int(durable_value), moderation_state),
                )
                row = con.execute("SELECT * FROM shared_sky_realtime_events WHERE event_id=?", (event_id,)).fetchone()
        except sqlite3.IntegrityError:
            if not idempotency_key:
                raise
            with self._connect() as con:
                row = con.execute(
                    """SELECT * FROM shared_sky_realtime_events
                       WHERE broadcast_id=? AND event_type=? AND idempotency_key=?""",
                    (broadcast_id, event_type, idempotency_key),
                ).fetchone()
        item = dict(row)
        item["payload"] = _json(item.pop("payload_json", "{}"), {})
        item["durable"] = bool(item["durable"])
        return item

    def events_after(self, broadcast_id: str, after: int = 0, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM shared_sky_realtime_events
                   WHERE broadcast_id=? AND seq>? ORDER BY seq ASC LIMIT ?""",
                (broadcast_id, max(0, after), max(1, min(limit, 500))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json(item.pop("payload_json", "{}"), {})
            item["durable"] = bool(item["durable"])
            out.append(item)
        return out

    def follow(self, follower_user_id: str, creator_user_id: str, following: bool, notify_live: bool | None = None) -> dict:
        if follower_user_id == creator_user_id:
            raise ValueError("You cannot follow yourself")
        if self.is_blocked(creator_user_id, follower_user_id):
            raise PermissionError("Follow is unavailable")
        now = _now()
        with self._connect() as con:
            if following:
                con.execute(
                    """INSERT INTO shared_sky_follows(follower_user_id,creator_user_id,notify_live,created_at,updated_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(follower_user_id,creator_user_id) DO UPDATE SET
                       notify_live=COALESCE(?,notify_live),updated_at=excluded.updated_at""",
                    (follower_user_id, creator_user_id, int(True if notify_live is None else notify_live),
                     now, now, None if notify_live is None else int(notify_live)),
                )
            else:
                con.execute(
                    "DELETE FROM shared_sky_follows WHERE follower_user_id=? AND creator_user_id=?",
                    (follower_user_id, creator_user_id),
                )
            row = con.execute(
                "SELECT COUNT(*) AS n FROM shared_sky_follows WHERE creator_user_id=?", (creator_user_id,)
            ).fetchone()
        return {"following": following, "creator_user_id": creator_user_id, "follower_count": int(row["n"] or 0)}

    def notification_preference(self, follower_user_id: str, creator_user_id: str, notify_live: bool) -> dict:
        with self._connect() as con:
            cur = con.execute(
                """UPDATE shared_sky_follows SET notify_live=?,updated_at=?
                   WHERE follower_user_id=? AND creator_user_id=?""",
                (int(notify_live), _now(), follower_user_id, creator_user_id),
            )
        if cur.rowcount < 1:
            raise KeyError("Follow relationship not found")
        return {"creator_user_id": creator_user_id, "notify_live": notify_live}

    def moderator_allowed(self, broadcast_id: str, user_id: str | None, owner: bool = False) -> bool:
        if owner:
            return True
        if not user_id:
            return False
        broadcast = self._broadcast(broadcast_id)
        if broadcast["user_id"] == user_id:
            return True
        with self._connect() as con:
            return bool(con.execute(
                "SELECT 1 FROM shared_sky_live_moderators WHERE broadcast_id=? AND user_id=?",
                (broadcast_id, user_id),
            ).fetchone())

    def chat_settings(self, broadcast_id: str) -> dict:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO shared_sky_chat_settings(broadcast_id,updated_at) VALUES(?,?)",
                (broadcast_id, _now()),
            )
            row = con.execute("SELECT * FROM shared_sky_chat_settings WHERE broadcast_id=?", (broadcast_id,)).fetchone()
        item = dict(row)
        for key in ("enabled", "followers_only", "members_only"):
            item[key] = bool(item[key])
        return item

    def _active_ban(self, broadcast_id: str, user_id: str) -> dict | None:
        now = _now()
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM shared_sky_live_bans WHERE broadcast_id=? AND user_id=?
                   AND action IN ('timeout','remove') AND (expires_at IS NULL OR expires_at>?)
                   ORDER BY created_at DESC LIMIT 1""",
                (broadcast_id, user_id, now),
            ).fetchone()
        return dict(row) if row else None

    def send_chat(self, broadcast_id: str, sender_user_id: str, body: ChatSendRequest) -> dict:
        broadcast = self._broadcast(broadcast_id)
        if broadcast["state"] != "live":
            raise RuntimeError("LIVE chat has ended")
        decision = self.access(broadcast_id, sender_user_id, direct=True)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        if self._active_ban(broadcast_id, sender_user_id):
            raise PermissionError("You are temporarily unable to chat in this LIVE")
        settings = self.chat_settings(broadcast_id)
        if not settings["enabled"]:
            raise PermissionError("Chat is disabled")
        if settings["followers_only"] and not self.is_following(sender_user_id, broadcast["user_id"]) and sender_user_id != broadcast["user_id"]:
            raise PermissionError("Chat is followers-only")
        if settings["members_only"] and not sender_user_id:
            raise PermissionError("Chat is members-only")
        text = _clean_text(body.message, 1000)
        if not text:
            raise ValueError("Message is empty")
        self.rate_limit(f"user:{sender_user_id}", "chat", limit=20, window_seconds=30)
        with self._connect() as con:
            existing = con.execute(
                """SELECT * FROM shared_sky_chat_messages
                   WHERE broadcast_id=? AND sender_user_id=? AND idempotency_key=?""",
                (broadcast_id, sender_user_id, body.idempotency_key),
            ).fetchone()
            if existing:
                return self._chat_public(dict(existing))
            if settings["slow_seconds"] > 0 and sender_user_id != broadcast["user_id"]:
                last = con.execute(
                    """SELECT created_at FROM shared_sky_chat_messages
                       WHERE broadcast_id=? AND sender_user_id=? ORDER BY created_at DESC LIMIT 1""",
                    (broadcast_id, sender_user_id),
                ).fetchone()
                if last:
                    elapsed = (datetime.now(timezone.utc) - (_parse_dt(last["created_at"]) or datetime.now(timezone.utc))).total_seconds()
                    if elapsed < settings["slow_seconds"]:
                        raise PermissionError(f"Slow mode: wait {settings['slow_seconds']} seconds between messages")
            if body.reply_to_id:
                parent = con.execute(
                    "SELECT id FROM shared_sky_chat_messages WHERE id=? AND broadcast_id=? AND deleted_at IS NULL",
                    (body.reply_to_id, broadcast_id),
                ).fetchone()
                if not parent:
                    raise ValueError("Reply target is unavailable")
            message_id = uuid4().hex
            now = _now()
            con.execute(
                """INSERT INTO shared_sky_chat_messages
                   (id,broadcast_id,sender_user_id,body,reply_to_id,idempotency_key,created_at,links_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (message_id, broadcast_id, sender_user_id, text, body.reply_to_id,
                 body.idempotency_key, now, json.dumps(_safe_links(text))),
            )
            row = con.execute("SELECT * FROM shared_sky_chat_messages WHERE id=?", (message_id,)).fetchone()
        message = self._chat_public(dict(row))
        self.emit(
            broadcast_id, sender_user_id, "chat.message", {"message": message},
            idempotency_key=f"chat-event:{sender_user_id}:{body.idempotency_key}",
        )
        return message

    @staticmethod
    def _chat_public(item: dict) -> dict:
        item["links"] = _json(item.pop("links_json", "[]"), [])
        item["deleted"] = bool(item.get("deleted_at"))
        item["pinned"] = bool(item.get("pinned_at"))
        if item["deleted"]:
            item["body"] = ""
        return item

    def chat_history(self, broadcast_id: str, *, before: str | None = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM shared_sky_chat_messages WHERE broadcast_id=?"
        args: list[Any] = [broadcast_id]
        if before:
            query += " AND created_at<?"
            args.append(before)
        query += " ORDER BY created_at DESC,id DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))
        with self._connect() as con:
            rows = con.execute(query, tuple(args)).fetchall()
        return [self._chat_public(dict(row)) for row in reversed(rows)]

    def react(self, broadcast_id: str, actor_key: str, actor_user_id: str | None, body: ReactionRequest) -> dict:
        if body.reaction not in REACTION_TYPES:
            raise ValueError("Unknown reaction")
        broadcast = self._broadcast(broadcast_id)
        if broadcast["state"] != "live":
            raise RuntimeError("LIVE has ended")
        self.rate_limit(actor_key, "reaction", limit=80, window_seconds=10)
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO shared_sky_engagement_receipts
                       (broadcast_id,actor_key,action_type,idempotency_key,amount,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (broadcast_id, actor_key, f"reaction:{body.reaction}", body.idempotency_key, body.amount, _now()),
                )
                con.execute(
                    """INSERT INTO shared_sky_reaction_totals(broadcast_id,reaction_type,total,updated_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(broadcast_id,reaction_type) DO UPDATE SET
                       total=total+excluded.total,updated_at=excluded.updated_at""",
                    (broadcast_id, body.reaction, body.amount, _now()),
                )
                accepted = True
            except sqlite3.IntegrityError:
                accepted = False
            row = con.execute(
                "SELECT total FROM shared_sky_reaction_totals WHERE broadcast_id=? AND reaction_type=?",
                (broadcast_id, body.reaction),
            ).fetchone()
        total = int(row["total"] or 0) if row else 0
        if accepted:
            self.emit(
                broadcast_id, actor_user_id, "reaction.aggregate",
                {"reaction": body.reaction, "amount": body.amount, "total": total},
                idempotency_key=f"reaction-event:{actor_key}:{body.idempotency_key}", durable=False,
            )
        return {"accepted": accepted, "reaction": body.reaction, "total": total}

    def share(self, broadcast_id: str, actor_key: str, actor_user_id: str | None, body: ShareRequest) -> dict:
        self.rate_limit(actor_key, "share", limit=20, window_seconds=60)
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO shared_sky_engagement_receipts
                       (broadcast_id,actor_key,action_type,idempotency_key,amount,created_at)
                       VALUES(?,?,?,?,1,?)""",
                    (broadcast_id, actor_key, f"share:{body.action}", body.idempotency_key, _now()),
                )
                accepted = True
            except sqlite3.IntegrityError:
                accepted = False
        if accepted:
            self.emit(
                broadcast_id, actor_user_id, "share.intent",
                {"action": body.action, "destination": body.destination, "external_post_confirmed": False},
                idempotency_key=f"share-event:{actor_key}:{body.idempotency_key}", durable=False,
            )
        return {"accepted": accepted, "action": body.action, "external_post_confirmed": False, "analytics_semantics": "intent_or_share_surface_open_only"}

    def create_poll(self, broadcast_id: str, actor_user_id: str, body: PollCreateRequest) -> dict:
        if not self.moderator_allowed(broadcast_id, actor_user_id):
            raise PermissionError("Creator or moderator permission required")
        question = _clean_text(body.question, 240)
        options = [_clean_text(option, 120) for option in body.options]
        if len(set(o.casefold() for o in options if o)) < 2:
            raise ValueError("Poll requires at least two distinct options")
        with self._connect() as con:
            existing = con.execute(
                "SELECT id FROM shared_sky_polls WHERE broadcast_id=? AND created_by=? AND idempotency_key=?",
                (broadcast_id, actor_user_id, body.idempotency_key),
            ).fetchone()
            if existing:
                return self.poll(str(existing["id"]), actor_user_id)
            poll_id = uuid4().hex
            starts = datetime.now(timezone.utc)
            ends = (starts + timedelta(seconds=body.duration_seconds)).isoformat() if body.duration_seconds else None
            con.execute(
                """INSERT INTO shared_sky_polls
                   (id,broadcast_id,question,multiple_choice,hide_until_voted,hide_until_ended,state,
                    created_by,starts_at,ends_at,idempotency_key)
                   VALUES(?,?,?,?,?,?,'live',?,?,?,?)""",
                (poll_id, broadcast_id, question, int(body.multiple_choice), int(body.hide_until_voted),
                 int(body.hide_until_ended), actor_user_id, starts.isoformat(), ends, body.idempotency_key),
            )
            for position, label in enumerate(options):
                con.execute(
                    "INSERT INTO shared_sky_poll_options(id,poll_id,label,position) VALUES(?,?,?,?)",
                    (uuid4().hex, poll_id, label, position),
                )
        poll = self.poll(poll_id, actor_user_id)
        self.emit(
            broadcast_id, actor_user_id, "poll.created", {"poll": poll},
            idempotency_key=f"poll-created:{actor_user_id}:{body.idempotency_key}",
        )
        return poll

    def poll(self, poll_id: str, viewer_key: str | None = None) -> dict:
        with self._connect() as con:
            poll = con.execute("SELECT * FROM shared_sky_polls WHERE id=?", (poll_id,)).fetchone()
            if not poll:
                raise KeyError(poll_id)
            options = con.execute(
                """SELECT o.id,o.label,o.position,COUNT(v.option_id) AS votes
                   FROM shared_sky_poll_options o
                   LEFT JOIN shared_sky_poll_votes v ON v.option_id=o.id
                   WHERE o.poll_id=? GROUP BY o.id ORDER BY o.position""",
                (poll_id,),
            ).fetchall()
            voted = False
            if viewer_key:
                voted = bool(con.execute(
                    "SELECT 1 FROM shared_sky_poll_votes WHERE poll_id=? AND voter_key=? LIMIT 1",
                    (poll_id, viewer_key),
                ).fetchone())
        item = dict(poll)
        now = datetime.now(timezone.utc)
        if item["state"] == "live" and item.get("ends_at") and (_parse_dt(item["ends_at"]) or now) <= now:
            item["state"] = "ended"
            with self._connect() as con:
                con.execute("UPDATE shared_sky_polls SET state='ended',closed_at=? WHERE id=?", (_now(), poll_id))
        show_results = item["state"] != "live" or (not bool(item["hide_until_voted"]) or voted)
        if bool(item["hide_until_ended"]) and item["state"] == "live":
            show_results = False
        item["options"] = [
            {"id": row["id"], "label": row["label"], "position": row["position"], "votes": int(row["votes"] or 0) if show_results else None}
            for row in options
        ]
        item["voted"] = voted
        item["results_visible"] = show_results
        item["multiple_choice"] = bool(item["multiple_choice"])
        item["hide_until_voted"] = bool(item["hide_until_voted"])
        item["hide_until_ended"] = bool(item["hide_until_ended"])
        return item

    def vote_poll(self, poll_id: str, voter_key: str, actor_user_id: str | None, body: PollVoteRequest) -> dict:
        poll = self.poll(poll_id, voter_key)
        if poll["state"] != "live":
            raise RuntimeError("Poll is closed")
        option_ids = list(dict.fromkeys(body.option_ids))
        if not poll["multiple_choice"] and len(option_ids) != 1:
            raise ValueError("Choose one option")
        allowed = {option["id"] for option in poll["options"]}
        if any(option_id not in allowed for option_id in option_ids):
            raise ValueError("Unknown poll option")
        self.rate_limit(voter_key, "poll_vote", limit=8, window_seconds=60)
        with self._connect() as con:
            already = con.execute(
                "SELECT 1 FROM shared_sky_poll_votes WHERE poll_id=? AND voter_key=? LIMIT 1",
                (poll_id, voter_key),
            ).fetchone()
            if already:
                return self.poll(poll_id, voter_key)
            for option_id in option_ids:
                con.execute(
                    """INSERT INTO shared_sky_poll_votes(poll_id,voter_key,option_id,idempotency_key,created_at)
                       VALUES(?,?,?,?,?)""",
                    (poll_id, voter_key, option_id, body.idempotency_key, _now()),
                )
        updated = self.poll(poll_id, voter_key)
        self.emit(
            poll["broadcast_id"], actor_user_id, "poll.voted",
            {"poll_id": poll_id, "results": updated["options"] if updated["results_visible"] else None},
            idempotency_key=f"poll-vote:{poll_id}:{voter_key}:{body.idempotency_key}",
        )
        return updated

    def submit_qa(self, broadcast_id: str, user_id: str, body: QARequest) -> dict:
        text = _clean_text(body.question, 600)
        if not text:
            raise ValueError("Question is empty")
        self.rate_limit(f"user:{user_id}", "qa", limit=5, window_seconds=60)
        with self._connect() as con:
            existing = con.execute(
                """SELECT * FROM shared_sky_qa WHERE broadcast_id=? AND asker_user_id=? AND idempotency_key=?""",
                (broadcast_id, user_id, body.idempotency_key),
            ).fetchone()
            if existing:
                return dict(existing)
            item_id = uuid4().hex
            now = _now()
            con.execute(
                """INSERT INTO shared_sky_qa
                   (id,broadcast_id,asker_user_id,question,state,selected,idempotency_key,created_at,updated_at)
                   VALUES(?,?,?,?,'pending',0,?,?,?)""",
                (item_id, broadcast_id, user_id, text, body.idempotency_key, now, now),
            )
            row = con.execute("SELECT * FROM shared_sky_qa WHERE id=?", (item_id,)).fetchone()
        item = dict(row)
        self.emit(
            broadcast_id, user_id, "qa.submitted", {"question_id": item_id, "state": "pending"},
            idempotency_key=f"qa-submit:{user_id}:{body.idempotency_key}", audience="moderators",
        )
        return item

    def moderate_qa(self, broadcast_id: str, question_id: str, actor_user_id: str, body: QAModerationRequest) -> dict:
        if not self.moderator_allowed(broadcast_id, actor_user_id):
            raise PermissionError("Creator or moderator permission required")
        states = {"approve": "approved", "reject": "rejected", "select": "approved", "answered": "answered", "remove": "removed"}
        state = states[body.action]
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_qa WHERE id=? AND broadcast_id=?", (question_id, broadcast_id)).fetchone()
            if not row:
                raise KeyError(question_id)
            if body.action == "select":
                con.execute("UPDATE shared_sky_qa SET selected=0 WHERE broadcast_id=?", (broadcast_id,))
                con.execute("UPDATE shared_sky_qa SET state='approved',selected=1,updated_at=? WHERE id=?", (_now(), question_id))
            else:
                con.execute("UPDATE shared_sky_qa SET state=?,selected=0,updated_at=? WHERE id=?", (state, _now(), question_id))
            updated = con.execute("SELECT * FROM shared_sky_qa WHERE id=?", (question_id,)).fetchone()
        item = dict(updated)
        self.emit(
            broadcast_id, actor_user_id, f"qa.{state}", {"question": item},
            idempotency_key=f"qa-mod:{actor_user_id}:{body.idempotency_key}",
        )
        return item

    def report(self, broadcast_id: str, reporter_user_id: str | None, body: ReportRequest) -> dict:
        report_id = uuid4().hex
        audit_id = uuid4().hex
        evidence: dict[str, Any] = {}
        if body.message_id:
            with self._connect() as con:
                msg = con.execute(
                    "SELECT id,sender_user_id,body,created_at FROM shared_sky_chat_messages WHERE id=? AND broadcast_id=?",
                    (body.message_id, broadcast_id),
                ).fetchone()
            if msg:
                evidence["message"] = {"id": msg["id"], "sender_user_id": msg["sender_user_id"], "body": str(msg["body"])[:1000], "created_at": msg["created_at"]}
        with self._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_reports
                   (id,broadcast_id,reporter_user_id,target_user_id,message_id,category,reason,
                    evidence_json,created_at,audit_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (report_id, broadcast_id, reporter_user_id, body.target_user_id, body.message_id,
                 _clean_text(body.category, 80), _clean_text(body.reason, 1000), json.dumps(evidence), _now(), audit_id),
            )
        return {"report_id": report_id, "audit_id": audit_id, "state": "submitted"}

    def moderate(self, broadcast_id: str, actor_user_id: str | None, owner: bool, body: ModerationRequest) -> dict:
        if not self.moderator_allowed(broadcast_id, actor_user_id, owner):
            raise PermissionError("Creator or moderator permission required")
        actor_kind = "owner" if owner else "member"
        actor = actor_user_id or "owner"
        with self._connect() as con:
            existing = con.execute(
                """SELECT * FROM shared_sky_moderation_actions
                   WHERE broadcast_id=? AND actor_kind=? AND actor_user_id IS ? AND idempotency_key=?""",
                (broadcast_id, actor_kind, actor_user_id, body.idempotency_key),
            ).fetchone()
            if existing:
                return dict(existing)
        settings = self.chat_settings(broadcast_id)
        if body.expected_version is not None and body.action.startswith("set_") and settings["version"] != body.expected_version:
            raise RuntimeError("Chat settings changed; refresh before editing")
        payload: dict[str, Any] = {}
        now = _now()
        with self._connect() as con:
            if body.action in {"pin_message", "unpin_message", "delete_message"}:
                if not body.message_id:
                    raise ValueError("message_id is required")
                msg = con.execute("SELECT id FROM shared_sky_chat_messages WHERE id=? AND broadcast_id=?", (body.message_id, broadcast_id)).fetchone()
                if not msg:
                    raise KeyError(body.message_id)
                if body.action == "pin_message":
                    con.execute("UPDATE shared_sky_chat_messages SET pinned_at=? WHERE id=?", (now, body.message_id))
                elif body.action == "unpin_message":
                    con.execute("UPDATE shared_sky_chat_messages SET pinned_at=NULL WHERE id=?", (body.message_id,))
                else:
                    con.execute("UPDATE shared_sky_chat_messages SET deleted_at=?,moderation_state='removed' WHERE id=?", (now, body.message_id))
            elif body.action in {"timeout_user", "remove_user"}:
                if not body.target_user_id:
                    raise ValueError("target_user_id is required")
                expires = None
                action = "remove"
                if body.action == "timeout_user":
                    action = "timeout"
                    expires = (datetime.now(timezone.utc) + timedelta(seconds=body.duration_seconds or 300)).isoformat()
                con.execute(
                    """INSERT INTO shared_sky_live_bans
                       (broadcast_id,user_id,action,expires_at,reason,created_at,created_by)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(broadcast_id,user_id,action) DO UPDATE SET
                       expires_at=excluded.expires_at,reason=excluded.reason,created_at=excluded.created_at,created_by=excluded.created_by""",
                    (broadcast_id, body.target_user_id, action, expires, _clean_text(body.reason, 500), now, actor),
                )
                if body.action == "remove_user":
                    con.execute("DELETE FROM shared_sky_presence WHERE broadcast_id=? AND viewer_user_id=?", (broadcast_id, body.target_user_id))
                payload["expires_at"] = expires
            elif body.action in {"block_user", "unblock_user"}:
                if not body.target_user_id:
                    raise ValueError("target_user_id is required")
                creator_id = self._broadcast(broadcast_id)["user_id"]
                if body.action == "block_user":
                    con.execute(
                        """INSERT OR REPLACE INTO shared_sky_blocks(blocker_user_id,blocked_user_id,created_at,reason)
                           VALUES(?,?,?,?)""",
                        (creator_id, body.target_user_id, now, _clean_text(body.reason, 500)),
                    )
                    con.execute("DELETE FROM shared_sky_follows WHERE follower_user_id=? AND creator_user_id=?", (body.target_user_id, creator_id))
                    con.execute("DELETE FROM shared_sky_presence WHERE broadcast_id=? AND viewer_user_id=?", (broadcast_id, body.target_user_id))
                else:
                    con.execute("DELETE FROM shared_sky_blocks WHERE blocker_user_id=? AND blocked_user_id=?", (creator_id, body.target_user_id))
            else:
                field_map = {"set_slow_mode": "slow_seconds", "set_chat_enabled": "enabled", "set_followers_only": "followers_only", "set_members_only": "members_only"}
                field = field_map[body.action]
                value = max(0, min(int(body.value or 0), 300)) if field == "slow_seconds" else int(bool(body.value))
                con.execute(f"UPDATE shared_sky_chat_settings SET {field}=?,version=version+1,updated_at=? WHERE broadcast_id=?", (value, now, broadcast_id))
                payload["value"] = value
            action_id = uuid4().hex
            correlation_id = uuid4().hex
            con.execute(
                """INSERT INTO shared_sky_moderation_actions
                   (id,broadcast_id,actor_user_id,actor_kind,action,target_user_id,message_id,reason,
                    payload_json,correlation_id,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (action_id, broadcast_id, actor_user_id, actor_kind, body.action, body.target_user_id,
                 body.message_id, _clean_text(body.reason, 500), json.dumps(payload), correlation_id,
                 body.idempotency_key, now),
            )
            row = con.execute("SELECT * FROM shared_sky_moderation_actions WHERE id=?", (action_id,)).fetchone()
        event_type = "chat.settings" if body.action.startswith("set_") else "moderation.action"
        self.emit(
            broadcast_id, actor_user_id, event_type,
            {"action": body.action, "target_user_id": body.target_user_id, "message_id": body.message_id, **payload},
            idempotency_key=f"moderation-event:{actor}:{body.idempotency_key}", audience="room", correlation_id=correlation_id,
        )
        return dict(row)

    def watch(self, broadcast_id: str, user_id: str | None) -> None:
        if not user_id:
            return
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_watch_history(user_id,broadcast_id,viewed_at,last_seen_at)
                   VALUES(?,?,?,?) ON CONFLICT(user_id,broadcast_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                (user_id, broadcast_id, now, now),
            )

    def clear_watch_history(self, user_id: str) -> int:
        with self._connect() as con:
            cur = con.execute("DELETE FROM shared_sky_watch_history WHERE user_id=?", (user_id,))
        return int(cur.rowcount)

    def directory(self, viewer_user_id: str | None, *, category: str | None = None, q: str = "", language: str | None = None, following_only: bool = False, captions: bool | None = None, limit: int = 50, offset: int = 0, owner: bool = False) -> list[dict]:
        self.reconcile()
        with self._connect() as con:
            rows = con.execute(
                """SELECT b.id,b.user_id,b.project_id,b.title,b.description,b.state,b.started_at,b.updated_at,
                          u.display_name AS creator_display_name,d.category,d.tags_json,d.language,d.visibility,
                          d.suitability,d.thumbnail_url,d.captions_available,d.scheduled_event_id,d.version
                   FROM shared_sky_broadcasts b JOIN shared_sky_live_directory d ON d.broadcast_id=b.id
                   JOIN users u ON u.id=b.user_id WHERE b.state='live'
                   ORDER BY b.started_at DESC LIMIT 500"""
            ).fetchall()
        query = q.strip().casefold()
        results: list[dict] = []
        now = datetime.now(timezone.utc)
        for row in rows:
            item = dict(row)
            decision = self.access(item["id"], viewer_user_id, direct=False, owner=owner)
            if not decision.allowed or not decision.discoverable:
                continue
            if following_only and not self.is_following(viewer_user_id, item["user_id"]):
                continue
            if category and category not in {"recommended", "all"} and item["category"] != category:
                continue
            if language and item["language"] != language.lower():
                continue
            if captions is True and not bool(item["captions_available"]):
                continue
            tags = _json(item.pop("tags_json", "[]"), [])
            if query and query not in " ".join([item["title"], item["creator_display_name"], item["category"], *tags]).casefold():
                continue
            playback = _playback_adapter.descriptor(item["id"], viewer_user_id)
            if not playback.get("available") or playback.get("state") in {"ended", "unavailable", "failed"}:
                continue
            started = _parse_dt(item["started_at"])
            age_seconds = max(0.0, (now - started).total_seconds()) if started else 0.0
            followed = self.is_following(viewer_user_id, item["user_id"])
            freshness = max(0.0, 3600.0 - min(age_seconds, 3600.0)) / 3600.0
            score = (100.0 if followed else 0.0) + (10.0 * freshness)
            item.update({
                "tags": tags, "captions_available": bool(item["captions_available"]),
                "viewer_count": self.presence_count(item["id"]), "following": followed,
                "duration_seconds": int(age_seconds), "playback_state": playback.get("state", "ready"),
                "rank_score": round(score, 4), "rank_model": "deterministic_follow_plus_freshness_v1", "verified": None,
            })
            results.append(item)
        results.sort(key=lambda x: (-x["rank_score"], -x["viewer_count"], x["started_at"] or ""))
        return results[max(0, offset): max(0, offset) + max(1, min(limit, 100))]

    def detail(self, broadcast_id: str, request: Request, viewer_user_id: str | None) -> dict:
        self.reconcile()
        decision = self.access(broadcast_id, viewer_user_id, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        broadcast = self._broadcast(broadcast_id)
        meta = self._directory_row(broadcast_id) or {}
        playback = _playback_adapter.descriptor(broadcast_id, viewer_user_id)
        live_state = "degraded" if broadcast["state"] == "live" and (not playback.get("available") or playback.get("state") in {"ended", "failed"}) else broadcast["state"]
        reactions = {}
        with self._connect() as con:
            for row in con.execute("SELECT reaction_type,total FROM shared_sky_reaction_totals WHERE broadcast_id=?", (broadcast_id,)).fetchall():
                reactions[row["reaction_type"]] = int(row["total"] or 0)
        replay = _playback_adapter.replay(broadcast_id, viewer_user_id) if broadcast["state"] == "ended" else None
        return {
            "broadcast": {"id": broadcast["id"], "creator_user_id": broadcast["user_id"], "creator_display_name": broadcast["creator_display_name"], "title": broadcast["title"], "description": broadcast["description"], "state": live_state, "authoritative_state": broadcast["state"], "started_at": broadcast["started_at"], "ended_at": broadcast["ended_at"]},
            "metadata": meta, "viewer_count": self.presence_count(broadcast_id) if broadcast["state"] == "live" else 0,
            "count_definition": "current unexpired unique Shared Sky presence leases", "following": self.is_following(viewer_user_id, broadcast["user_id"]),
            "playback": playback, "replay": replay, "chat_settings": self.chat_settings(broadcast_id), "reactions": reactions,
            "gift_display": _gift_adapter.state(broadcast_id, viewer_user_id), "battle_display": _battle_adapter.state(broadcast_id, viewer_user_id),
        }


community = LiveCommunityStore()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Sky LIVE not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "Shared Sky LIVE community operation failed")


@router.get("/shared-sky/live/api/directory")
def live_directory(request: Request, q: str = "", category: str | None = None, language: str | None = None, following: bool = False, captions: bool | None = None, limit: int = 50, offset: int = 0):
    member = optional_member(request)
    user_id = member.user_id if member else None
    if following and not user_id:
        raise HTTPException(401, "Sign in required for Following Live")
    rows = community.directory(user_id, category=category, q=q, language=language, following_only=following, captions=captions, limit=limit, offset=offset, owner=owner_session_authorized(request))
    return {"live": rows, "next_offset": offset + len(rows) if len(rows) == limit else None}


@router.get("/shared-sky/live/api/watch/{broadcast_id}")
def watch_state(broadcast_id: str, request: Request):
    member = optional_member(request)
    try:
        return community.detail(broadcast_id, request, member.user_id if member else None)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/shared-sky/live/api/watch/{broadcast_id}/metadata")
def patch_live_metadata(broadcast_id: str, body: LiveMetadataPatch, request: Request):
    member = require_member(request)
    try:
        return community.update_metadata(broadcast_id, member.user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/presence")
def join_live_presence(broadcast_id: str, body: PresenceJoinRequest, request: Request):
    member = optional_member(request)
    try:
        return community.join_presence(broadcast_id, request, member.user_id if member else None, body.resume_token)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/presence/heartbeat")
def heartbeat_live_presence(broadcast_id: str, body: PresenceHeartbeatRequest):
    try:
        return community.heartbeat(broadcast_id, body.presence_id, body.resume_token)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/shared-sky/live/api/watch/{broadcast_id}/presence/{presence_id}")
def leave_live_presence(broadcast_id: str, presence_id: str, resume_token: str):
    try:
        community.leave_presence(broadcast_id, presence_id, resume_token)
        return {"left": True}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/live/api/watch/{broadcast_id}/events")
def live_events(broadcast_id: str, request: Request, after: int = 0):
    member = optional_member(request)
    try:
        decision = community.access(broadcast_id, member.user_id if member else None, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
    except Exception as exc:
        raise _http_error(exc) from exc

    def stream():
        cursor = max(0, after)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            events = community.events_after(broadcast_id, cursor, 200)
            if events:
                for event in events:
                    cursor = int(event["seq"])
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            else:
                yield ": keepalive\n\n"
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/shared-sky/live/api/watch/{broadcast_id}/chat")
def live_chat_history(broadcast_id: str, request: Request, before: str | None = None, limit: int = 100):
    member = optional_member(request)
    try:
        decision = community.access(broadcast_id, member.user_id if member else None, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return {"messages": community.chat_history(broadcast_id, before=before, limit=limit)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/chat")
def send_live_chat(broadcast_id: str, body: ChatSendRequest, request: Request):
    member = require_member(request)
    try:
        return community.send_chat(broadcast_id, member.user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/reactions")
def react_live(broadcast_id: str, body: ReactionRequest, request: Request):
    member = optional_member(request)
    user_id = member.user_id if member else None
    actor_key = f"user:{user_id}" if user_id else community.presence_actor(broadcast_id, body.presence_token)
    if not actor_key:
        raise HTTPException(401, "Join the LIVE before reacting")
    try:
        return community.react(broadcast_id, actor_key, user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/share")
def share_live(broadcast_id: str, body: ShareRequest, request: Request):
    member = optional_member(request)
    user_id = member.user_id if member else None
    actor_key = community.actor_key(request, user_id)
    try:
        decision = community.access(broadcast_id, user_id, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return community.share(broadcast_id, actor_key, user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/shared-sky/live/api/creators/{creator_user_id}/follow")
def follow_creator(creator_user_id: str, body: FollowRequest, request: Request):
    member = require_member(request)
    try:
        community.rate_limit(f"user:{member.user_id}", "follow", limit=30, window_seconds=60)
        return community.follow(member.user_id, creator_user_id, body.following, body.notify_live)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/shared-sky/live/api/creators/{creator_user_id}/notifications")
def creator_notification_preference(creator_user_id: str, body: NotificationPreferenceRequest, request: Request):
    member = require_member(request)
    try:
        return community.notification_preference(member.user_id, creator_user_id, body.notify_live)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/polls")
def create_live_poll(broadcast_id: str, body: PollCreateRequest, request: Request):
    member = require_member(request)
    try:
        return community.create_poll(broadcast_id, member.user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/polls/{poll_id}/vote")
def vote_live_poll(poll_id: str, body: PollVoteRequest, request: Request):
    member = optional_member(request)
    user_id = member.user_id if member else None
    try:
        poll = community.poll(poll_id)
        decision = community.access(poll["broadcast_id"], user_id, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        voter_key = f"user:{user_id}" if user_id else community.presence_actor(poll["broadcast_id"], body.presence_token)
        if not voter_key:
            raise PermissionError("Join the LIVE before voting")
        return community.vote_poll(poll_id, voter_key, user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/qa")
def submit_live_qa(broadcast_id: str, body: QARequest, request: Request):
    member = require_member(request)
    try:
        decision = community.access(broadcast_id, member.user_id, direct=True)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return community.submit_qa(broadcast_id, member.user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/qa/{question_id}")
def moderate_live_qa(broadcast_id: str, question_id: str, body: QAModerationRequest, request: Request):
    member = require_member(request)
    try:
        return community.moderate_qa(broadcast_id, question_id, member.user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/report")
def report_live(broadcast_id: str, body: ReportRequest, request: Request):
    member = optional_member(request)
    user_id = member.user_id if member else None
    try:
        community.rate_limit(community.actor_key(request, user_id), "report", limit=8, window_seconds=300)
        decision = community.access(broadcast_id, user_id, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return community.report(broadcast_id, user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/live/api/watch/{broadcast_id}/moderation")
def moderate_live(broadcast_id: str, body: ModerationRequest, request: Request):
    member = optional_member(request)
    owner = owner_session_authorized(request)
    user_id = member.user_id if member else None
    if not owner and not user_id:
        raise HTTPException(401, "Creator, moderator or owner authentication required")
    try:
        return community.moderate(broadcast_id, user_id, owner, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/live/api/watch/{broadcast_id}/external-chat")
def external_chat_capabilities(broadcast_id: str, request: Request):
    member = optional_member(request)
    user_id = member.user_id if member else None
    try:
        detail = community.detail(broadcast_id, request, user_id)
    except Exception as exc:
        raise _http_error(exc) from exc
    links = detail["metadata"].get("external_chat_links") or {}
    capabilities = []
    for platform in PLATFORM_REGISTRY:
        state = _external_chat_adapter.capability(broadcast_id, platform["id"], user_id)
        deep_link = links.get(platform["id"])
        state["open_destination_chat"] = deep_link if deep_link and not state.get("read_supported") else None
        state["messages"] = []
        capabilities.append(state)
    return {"capabilities": capabilities}


@router.delete("/shared-sky/live/api/watch-history")
def clear_watch_history(request: Request):
    member = require_member(request)
    return {"deleted": community.clear_watch_history(member.user_id)}


LIVE_NOW_CSS = """
:root{--bg:#061411;--panel:#0b2520;--panel2:#102f29;--line:#ffffff20;--text:#f3fff9;--muted:#b6cbc5;--emerald:#53e3af;--sardonyx:#d58462;--gold:#f2cf79}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#0b5e4955,transparent 30%),radial-gradient(circle at 93% 0,#b85f4050,transparent 25%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}a{color:inherit}.wrap{width:min(1320px,calc(100% - 28px));margin:auto;padding:20px 0 60px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.brand{font-weight:950}.muted{color:var(--muted)}.filters{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.filters a,.btn{border:1px solid var(--line);background:#ffffff08;padding:9px 11px;border-radius:999px;text-decoration:none}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}.card{border:1px solid var(--line);border-radius:18px;background:#0b2520e8;overflow:hidden}.thumb{aspect-ratio:16/9;background:linear-gradient(135deg,#0c6e55,#7d4432);display:grid;place-items:center;overflow:hidden}.thumb img{width:100%;height:100%;object-fit:cover}.live{background:#b42941;color:white;border-radius:999px;padding:4px 8px;font-size:.72rem;font-weight:900}.body{padding:14px}.meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:.82rem;color:var(--muted)}.empty{padding:40px;border:1px solid var(--line);border-radius:18px;background:var(--panel)}
@media(max-width:600px){.wrap{width:min(100% - 18px,1320px)}.grid{grid-template-columns:1fr 1fr;gap:9px}.body{padding:10px}.card h3{font-size:.95rem}.meta{font-size:.72rem}}@media(max-width:420px){.grid{grid-template-columns:1fr}}
"""


@router.get("/live-now", response_class=HTMLResponse, include_in_schema=False)
def live_now_page(request: Request, category: str = "recommended", q: str = ""):
    member = optional_member(request)
    user_id = member.user_id if member else None
    rows = community.directory(user_id, category=category, q=q, limit=60, owner=owner_session_authorized(request))
    cards = "".join(
        f"""<article class='card'><a href='/watch/{escape(row['id'], quote=True)}' style='text-decoration:none'>
        <div class='thumb'>{f"<img src='{escape(row['thumbnail_url'], quote=True)}' alt=''>" if row['thumbnail_url'] else "<span class='live'>LIVE</span>"}</div>
        <div class='body'><div class='meta'><span class='live'>LIVE</span><span>{row['viewer_count']} watching</span><span>{row['duration_seconds']//60}m</span></div>
        <h3>{escape(row['title'])}</h3><div class='meta'><span>{escape(row['creator_display_name'])}</span><span>·</span><span>{escape(row['category'])}</span><span>·</span><span>{escape(row['language'])}</span></div></div>
        </a></article>""" for row in rows
    ) or "<div class='empty'><h2>No playable Shared Sky LIVE sessions match this view.</h2><p class='muted'>Live Now only shows sessions confirmed LIVE by the broadcast service and ready by the playback contract.</p></div>"
    cats = ["recommended", "music", "gaming", "video-film", "art-image", "education", "talk-community", "battles", "events"]
    filter_links = "".join(f"<a href='/live-now?category={escape(cat, quote=True)}'>{escape(cat.replace('-', ' ').title())}</a>" for cat in cats)
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Live Now · Shared Sky</title><style>{LIVE_NOW_CSS}</style></head><body><main class='wrap'>
        <div class='top'><div><div class='brand'>Shared Sky Streaming Studios</div><div class='muted'>Elevate Souls Productions</div></div><a class='btn' href='/'>Command Center</a></div>
        <h1>Live Now</h1><p class='muted'>Real first-party LIVE sessions. Viewer counts are current unexpired Shared Sky presence leases—never seeded or fabricated.</p>
        <form class='top' action='/live-now' method='get'><input type='hidden' name='category' value='{escape(category, quote=True)}'><label>Search LIVE <input name='q' value='{escape(q, quote=True)}' maxlength='120'></label><button class='btn'>Search</button></form>
        <nav class='filters' aria-label='LIVE categories'>{filter_links}</nav><section class='grid'>{cards}</section></main></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


WATCH_CSS = LIVE_NOW_CSS + """
.watch{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,.6fr);gap:14px}.player{background:#000;border:1px solid var(--line);border-radius:18px;overflow:hidden;min-height:280px;display:grid;place-items:center}.player video{width:100%;max-height:78vh;background:#000}.panel{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:14px}.chat{height:min(55vh,620px);overflow:auto;display:flex;flex-direction:column;gap:8px}.msg{padding:8px;border-radius:10px;background:#ffffff08}.controls{display:flex;gap:8px;flex-wrap:wrap}.controls button{border:1px solid var(--line);background:#ffffff09;color:white;border-radius:10px;padding:9px}.composer{display:flex;gap:8px;margin-top:8px}.composer input{flex:1;min-width:0;background:#061411;color:white;border:1px solid var(--line);border-radius:10px;padding:10px}.status{padding:14px;color:var(--muted);text-align:center}.sr{position:absolute;width:1px;height:1px;clip:rect(0,0,0,0);overflow:hidden}
@media(max-width:900px){.watch{grid-template-columns:1fr}.chat{height:42vh}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
"""


@router.get("/watch/{broadcast_id}", response_class=HTMLResponse, include_in_schema=False)
def watch_page(broadcast_id: str, request: Request):
    member = optional_member(request)
    try:
        detail = community.detail(broadcast_id, request, member.user_id if member else None)
    except Exception:
        return HTMLResponse("<h1>Shared Sky LIVE unavailable</h1>", status_code=404)
    b = detail["broadcast"]
    playback = detail["playback"]
    source = escape(str(playback["manifest_url"]), quote=True) if playback.get("available") and playback.get("manifest_url") else ""
    player = f"<video id='video' controls playsinline preload='metadata' src='{source}'></video>" if source else f"<div class='status'><h2>{'LIVE playback is temporarily unavailable' if b['authoritative_state']=='live' else 'This LIVE has ended'}</h2><p>{escape(str(playback.get('reason') or 'No active playback descriptor.'))}</p></div>"
    return HTMLResponse(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>{escape(b['title'])} · Shared Sky LIVE</title><style>{WATCH_CSS}</style></head><body><main class='wrap'>
        <div class='top'><a class='btn' href='/live-now'>← Live Now</a><div class='brand'>Shared Sky Streaming Studios</div></div>
        <section class='watch'><div><div class='player'>{player}</div><div class='panel'><div class='meta'><span class='live'>{escape(b['authoritative_state'].upper())}</span><span id='viewers'>{detail['viewer_count']} watching</span></div>
        <h1>{escape(b['title'])}</h1><p><b>{escape(b['creator_display_name'])}</b></p><p class='muted'>{escape(b['description'])}</p>
        <div class='controls'><button id='follow'>Follow</button><button id='like'>♥ Like</button><button id='share'>Share</button><button id='report'>Report</button></div><div id='announce' class='sr' aria-live='polite'></div></div></div>
        <aside class='panel'><h2>LIVE chat</h2><div id='chat' class='chat' role='log' aria-live='polite' aria-relevant='additions'></div>
        <form id='chatForm' class='composer'><label class='sr' for='chatInput'>Message</label><input id='chatInput' maxlength='1000' autocomplete='off' placeholder='Message Shared Sky chat'><button>Send</button></form></aside></section></main>
        <script>
        (()=>{{const id={broadcast_id!r};let presence=null,cursor=0;const announce=t=>document.getElementById('announce').textContent=t;
        async function req(path,opt={{}}){{const r=await fetch(path,{{credentials:'same-origin',headers:{{'Content-Type':'application/json'}},...opt}});let d={{}};try{{d=await r.json()}}catch(e){{}}if(!r.ok)throw new Error(d.detail||'Request failed');return d}}
        async function join(){{try{{presence=await req(`/shared-sky/live/api/watch/${{id}}/presence`,{{method:'POST',body:JSON.stringify({{resume_token:localStorage.getItem('sharedSkyPresence:'+id)}})}});localStorage.setItem('sharedSkyPresence:'+id,presence.resume_token);document.getElementById('viewers').textContent=presence.viewer_count+' watching';setInterval(heartbeat,20000)}}catch(e){{announce(e.message)}}}}
        async function heartbeat(){{if(!presence)return;try{{const d=await req(`/shared-sky/live/api/watch/${{id}}/presence/heartbeat`,{{method:'POST',body:JSON.stringify({{presence_id:presence.presence_id,resume_token:presence.resume_token}})}});document.getElementById('viewers').textContent=d.viewer_count+' watching'}}catch(e){{presence=null;join()}}}}
        function addMsg(m){{if(document.querySelector(`[data-msg="${{m.id}}"]`))return;const el=document.createElement('div');el.className='msg';el.dataset.msg=m.id;const who=document.createElement('b');who.textContent=m.sender_user_id.slice(0,8);const body=document.createElement('span');body.textContent=m.deleted?' [removed] ':' '+m.body;el.append(who,body);document.getElementById('chat').append(el)}}
        async function history(){{try{{const d=await req(`/shared-sky/live/api/watch/${{id}}/chat`);d.messages.forEach(addMsg)}}catch(e){{}}}}
        function events(){{const es=new EventSource(`/shared-sky/live/api/watch/${{id}}/events?after=${{cursor}}`,{{withCredentials:true}});es.addEventListener('chat.message',e=>{{const d=JSON.parse(e.data);cursor=Number(e.lastEventId||cursor);addMsg(d.payload.message)}});es.addEventListener('reaction.aggregate',e=>{{cursor=Number(e.lastEventId||cursor)}});es.onerror=()=>{{es.close();setTimeout(events,1500)}}}}
        document.getElementById('chatForm').onsubmit=async e=>{{e.preventDefault();const x=document.getElementById('chatInput');const v=x.value.trim();if(!v)return;try{{const m=await req(`/shared-sky/live/api/watch/${{id}}/chat`,{{method:'POST',body:JSON.stringify({{message:v,idempotency_key:crypto.randomUUID()}})}});x.value='';addMsg(m)}}catch(err){{announce(err.message)}}}};
        document.getElementById('like').onclick=async()=>{{if(!presence)return;try{{await req(`/shared-sky/live/api/watch/${{id}}/reactions`,{{method:'POST',body:JSON.stringify({{reaction:'like',amount:1,idempotency_key:crypto.randomUUID(),presence_token:presence.resume_token}})}})}}catch(e){{announce(e.message)}}}};
        document.getElementById('share').onclick=async()=>{{const url=location.href;try{{if(navigator.share)await navigator.share({{title:document.title,url}});else await navigator.clipboard.writeText(url);await req(`/shared-sky/live/api/watch/${{id}}/share`,{{method:'POST',body:JSON.stringify({{action:navigator.share?'share_sheet_open':'copy_link',idempotency_key:crypto.randomUUID()}})}});announce('Share option opened. External posting is not assumed.')}}catch(e){{}}}};
        document.getElementById('report').onclick=async()=>{{const reason=prompt('What would you like to report?');if(!reason)return;try{{await req(`/shared-sky/live/api/watch/${{id}}/report`,{{method:'POST',body:JSON.stringify({{category:'viewer_report',reason}})}});announce('Report submitted.')}}catch(e){{announce(e.message)}}}};
        document.getElementById('follow').onclick=async()=>{{try{{await req(`/shared-sky/live/api/creators/{escape(b['creator_user_id'], quote=True)}/follow`,{{method:'PUT',body:JSON.stringify({{following:true,notify_live:true,idempotency_key:crypto.randomUUID()}})}});announce('Following creator.')}}catch(e){{announce(e.message)}}}};
        join();history();events();window.addEventListener('pagehide',()=>{{if(presence)fetch(`/shared-sky/live/api/watch/${{id}}/presence/${{presence.presence_id}}?resume_token=${{encodeURIComponent(presence.resume_token)}}`,{{method:'DELETE',credentials:'same-origin',keepalive:true}}).catch(()=>{{}})}})
        }})();
        </script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "community", "LiveCommunityStore", "register_playback_adapter", "register_gift_display_adapter", "register_battle_display_adapter", "register_external_chat_adapter", "PlaybackAdapter", "GiftDisplayAdapter", "BattleDisplayAdapter", "ExternalChatAdapter"]
