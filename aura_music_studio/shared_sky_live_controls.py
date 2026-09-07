from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .owner_identity import owner_session_authorized
from .shared_sky_live_community import community, optional_member, require_member

router = APIRouter(tags=["Shared Sky Live Community Controls"])


class PollStateRequest(BaseModel):
    state: Literal["ended", "cancelled"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class LivePreferenceRequest(BaseModel):
    reduced_motion: bool | None = None
    hide_reactions: bool | None = None
    hide_gift_animations: bool | None = None
    profanity_filter: bool | None = None


class KeywordFilterRequest(BaseModel):
    terms: list[str] = Field(default_factory=list, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReplayProgressRequest(BaseModel):
    progress_seconds: float = Field(ge=0, le=60 * 60 * 24 * 30)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Sky LIVE resource not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "Shared Sky LIVE control operation failed")


def _ensure_schema() -> None:
    with community._connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS shared_sky_live_user_preferences (
                user_id TEXT PRIMARY KEY,
                reduced_motion INTEGER NOT NULL DEFAULT 0,
                hide_reactions INTEGER NOT NULL DEFAULT 0,
                hide_gift_animations INTEGER NOT NULL DEFAULT 0,
                profanity_filter INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS shared_sky_keyword_filters (
                broadcast_id TEXT NOT NULL,
                term_normalized TEXT NOT NULL,
                term_display TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(broadcast_id,term_normalized),
                FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS shared_sky_control_receipts (
                broadcast_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                control_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                PRIMARY KEY(broadcast_id,actor_user_id,control_type,idempotency_key),
                FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
            );
            """
        )


_ensure_schema()


def _preferences(user_id: str) -> dict:
    with community._connect() as con:
        con.execute(
            """INSERT OR IGNORE INTO shared_sky_live_user_preferences(user_id,updated_at)
               VALUES(?,?)""",
            (user_id, _now()),
        )
        row = con.execute("SELECT * FROM shared_sky_live_user_preferences WHERE user_id=?", (user_id,)).fetchone()
    item = dict(row)
    for key in ("reduced_motion", "hide_reactions", "hide_gift_animations", "profanity_filter"):
        item[key] = bool(item[key])
    return item


@router.get("/shared-sky/live/api/preferences")
def get_live_preferences(request: Request):
    member = require_member(request)
    return _preferences(member.user_id)


@router.put("/shared-sky/live/api/preferences")
def update_live_preferences(body: LivePreferenceRequest, request: Request):
    member = require_member(request)
    current = _preferences(member.user_id)
    values = {
        key: current[key] if getattr(body, key) is None else bool(getattr(body, key))
        for key in ("reduced_motion", "hide_reactions", "hide_gift_animations", "profanity_filter")
    }
    with community._connect() as con:
        con.execute(
            """UPDATE shared_sky_live_user_preferences
               SET reduced_motion=?,hide_reactions=?,hide_gift_animations=?,profanity_filter=?,updated_at=?
               WHERE user_id=?""",
            (int(values["reduced_motion"]), int(values["hide_reactions"]), int(values["hide_gift_animations"]),
             int(values["profanity_filter"]), _now(), member.user_id),
        )
    return _preferences(member.user_id)


@router.post("/shared-sky/live/api/polls/{poll_id}/state")
def set_poll_state(poll_id: str, body: PollStateRequest, request: Request):
    member = require_member(request)
    try:
        poll = community.poll(poll_id, f"user:{member.user_id}")
        broadcast_id = str(poll["broadcast_id"])
        if not community.moderator_allowed(broadcast_id, member.user_id, owner_session_authorized(request)):
            raise PermissionError("Creator or moderator permission required")
        with community._connect() as con:
            receipt = con.execute(
                """SELECT result_json FROM shared_sky_control_receipts
                   WHERE broadcast_id=? AND actor_user_id=? AND control_type='poll_state' AND idempotency_key=?""",
                (broadcast_id, member.user_id, body.idempotency_key),
            ).fetchone()
            if receipt:
                return json.loads(receipt["result_json"])
            now = _now()
            con.execute(
                "UPDATE shared_sky_polls SET state=?,closed_at=? WHERE id=? AND broadcast_id=?",
                (body.state, now, poll_id, broadcast_id),
            )
            result = {"poll_id": poll_id, "broadcast_id": broadcast_id, "state": body.state, "closed_at": now}
            con.execute(
                """INSERT INTO shared_sky_control_receipts
                   (broadcast_id,actor_user_id,control_type,idempotency_key,result_json,created_at)
                   VALUES(?,?,'poll_state',?,?,?)""",
                (broadcast_id, member.user_id, body.idempotency_key, json.dumps(result), now),
            )
        community.emit(
            broadcast_id,
            member.user_id,
            "poll.cancelled" if body.state == "cancelled" else "poll.closed",
            result,
            idempotency_key=f"poll-state:{member.user_id}:{body.idempotency_key}",
        )
        return result
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/shared-sky/live/api/watch/{broadcast_id}/keyword-filters")
def get_keyword_filters(broadcast_id: str, request: Request):
    member = require_member(request)
    try:
        if not community.moderator_allowed(broadcast_id, member.user_id, owner_session_authorized(request)):
            raise PermissionError("Creator or moderator permission required")
        with community._connect() as con:
            rows = con.execute(
                "SELECT term_display FROM shared_sky_keyword_filters WHERE broadcast_id=? ORDER BY term_display COLLATE NOCASE",
                (broadcast_id,),
            ).fetchall()
        return {"terms": [str(row["term_display"]) for row in rows], "enforcement": "configuration_hook"}
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/shared-sky/live/api/watch/{broadcast_id}/keyword-filters")
def set_keyword_filters(broadcast_id: str, body: KeywordFilterRequest, request: Request):
    member = require_member(request)
    try:
        if not community.moderator_allowed(broadcast_id, member.user_id, owner_session_authorized(request)):
            raise PermissionError("Creator or moderator permission required")
        cleaned: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in body.terms:
            display = " ".join(str(raw).split())[:80]
            normalized = display.casefold()
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append((normalized, display))
        with community._connect() as con:
            receipt = con.execute(
                """SELECT result_json FROM shared_sky_control_receipts
                   WHERE broadcast_id=? AND actor_user_id=? AND control_type='keyword_filters' AND idempotency_key=?""",
                (broadcast_id, member.user_id, body.idempotency_key),
            ).fetchone()
            if receipt:
                return json.loads(receipt["result_json"])
            con.execute("DELETE FROM shared_sky_keyword_filters WHERE broadcast_id=?", (broadcast_id,))
            now = _now()
            for normalized, display in cleaned:
                con.execute(
                    """INSERT INTO shared_sky_keyword_filters
                       (broadcast_id,term_normalized,term_display,created_by,created_at) VALUES(?,?,?,?,?)""",
                    (broadcast_id, normalized, display, member.user_id, now),
                )
            result = {"terms": [display for _, display in cleaned], "enforcement": "configuration_hook"}
            con.execute(
                """INSERT INTO shared_sky_control_receipts
                   (broadcast_id,actor_user_id,control_type,idempotency_key,result_json,created_at)
                   VALUES(?,?,'keyword_filters',?,?,?)""",
                (broadcast_id, member.user_id, body.idempotency_key, json.dumps(result), now),
            )
        community.emit(
            broadcast_id,
            member.user_id,
            "chat.settings",
            {"keyword_filter_count": len(cleaned), "enforcement": "configuration_hook"},
            idempotency_key=f"keyword-filters:{member.user_id}:{body.idempotency_key}",
            audience="moderators",
        )
        return result
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/shared-sky/live/api/watch-history")
def list_watch_history(request: Request, limit: int = 50):
    member = require_member(request)
    with community._connect() as con:
        rows = con.execute(
            """SELECT h.broadcast_id,h.viewed_at,h.last_seen_at,h.replay_progress_seconds,
                      b.title,b.user_id AS creator_user_id,b.state,b.started_at,b.ended_at
               FROM shared_sky_watch_history h
               JOIN shared_sky_broadcasts b ON b.id=h.broadcast_id
               WHERE h.user_id=? ORDER BY h.last_seen_at DESC LIMIT ?""",
            (member.user_id, max(1, min(int(limit), 200))),
        ).fetchall()
    visible: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            decision = community.access(item["broadcast_id"], member.user_id, direct=True, owner=owner_session_authorized(request))
        except KeyError:
            continue
        if decision.allowed:
            visible.append(item)
    return {"history": visible}


@router.patch("/shared-sky/live/api/watch-history/{broadcast_id}")
def update_replay_progress(broadcast_id: str, body: ReplayProgressRequest, request: Request):
    member = require_member(request)
    try:
        broadcast = community._broadcast(broadcast_id)
        if broadcast["state"] != "ended":
            raise ValueError("Replay progress is only stored for ended LIVE sessions")
        decision = community.access(broadcast_id, member.user_id, direct=True, owner=owner_session_authorized(request))
        if not decision.allowed:
            raise PermissionError(decision.reason)
        with community._connect() as con:
            cur = con.execute(
                """UPDATE shared_sky_watch_history SET replay_progress_seconds=?,last_seen_at=?
                   WHERE user_id=? AND broadcast_id=?""",
                (body.progress_seconds, _now(), member.user_id, broadcast_id),
            )
        if cur.rowcount < 1:
            raise KeyError(broadcast_id)
        return {"broadcast_id": broadcast_id, "replay_progress_seconds": body.progress_seconds}
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/shared-sky/live/api/watch-history/{broadcast_id}")
def delete_watch_history_item(broadcast_id: str, request: Request):
    member = require_member(request)
    with community._connect() as con:
        cur = con.execute(
            "DELETE FROM shared_sky_watch_history WHERE user_id=? AND broadcast_id=?",
            (member.user_id, broadcast_id),
        )
    return {"deleted": bool(cur.rowcount), "broadcast_id": broadcast_id}


__all__ = ["router", "PollStateRequest", "LivePreferenceRequest", "KeywordFilterRequest", "ReplayProgressRequest"]
