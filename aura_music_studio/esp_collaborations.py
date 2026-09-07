from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Collaborations & Battles"])
CollabType = Literal["battle", "cohost", "group_live", "music", "gaming", "content", "event"]
EventStatus = Literal["proposed", "accepted", "declined", "cancelled", "completed", "no_show"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _roles(membership: dict) -> set[str]:
    if membership.get("status") == "owner":
        return {"creator", "agent", "owner"}
    role = str(membership.get("roles") or "").lower()
    return {"creator", "agent"} if role == "both" else {role}


class ProfileRequest(BaseModel):
    opt_in: bool = True
    display_title: str = Field(default="", max_length=160)
    collaboration_types: list[CollabType] = Field(default_factory=list, max_length=20)
    niche_tags: list[str] = Field(default_factory=list, max_length=30)
    region_tags: list[str] = Field(default_factory=list, max_length=20)
    availability_note: str = Field(default="", max_length=1200)


class ProposalRequest(BaseModel):
    invited_creator_user_id: str = Field(min_length=1, max_length=128)
    kind: CollabType
    title: str = Field(min_length=2, max_length=220)
    starts_at: str | None = Field(default=None, max_length=80)
    notes: str = Field(default="", max_length=2000)


class EventStatusRequest(BaseModel):
    status: EventStatus
    note: str = Field(default="", max_length=2000)


class CollaborationStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self):
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_collaboration_profiles (
                    user_id TEXT PRIMARY KEY,
                    opt_in INTEGER NOT NULL DEFAULT 0,
                    display_title TEXT NOT NULL DEFAULT '',
                    collaboration_types_json TEXT NOT NULL DEFAULT '[]',
                    niche_tags_json TEXT NOT NULL DEFAULT '[]',
                    region_tags_json TEXT NOT NULL DEFAULT '[]',
                    availability_note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_collaboration_events (
                    id TEXT PRIMARY KEY,
                    proposing_creator_user_id TEXT NOT NULL,
                    invited_creator_user_id TEXT NOT NULL,
                    proposed_by_user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    starts_at TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    status_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(proposing_creator_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(invited_creator_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(proposed_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_collab_events_creator_a ON esp_collaboration_events(proposing_creator_user_id,status,starts_at);
                CREATE INDEX IF NOT EXISTS idx_collab_events_creator_b ON esp_collaboration_events(invited_creator_user_id,status,starts_at);
                """
            )

    @staticmethod
    def _decode_profile(row) -> dict:
        item = dict(row)
        item["opt_in"] = bool(item["opt_in"])
        for source, target in (("collaboration_types_json","collaboration_types"),("niche_tags_json","niche_tags"),("region_tags_json","region_tags")):
            try:
                item[target] = json.loads(item.pop(source) or "[]")
            except Exception:
                item[target] = []
        return item

    def _active_creator(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT u.id,u.display_name,e.roles,e.status,e.tiktok_handle,e.region,n.niche
                   FROM users u JOIN esp_memberships e ON e.user_id=u.id
                   LEFT JOIN esp_niche_profiles n ON n.user_id=u.id WHERE u.id=?""",
                (user_id,),
            ).fetchone()
        if row is None or row["status"] not in {"active", "owner"} or (row["status"] != "owner" and row["roles"] not in {"creator", "both"}):
            raise PermissionError("Active ESP Creator access is required")
        return dict(row)

    def set_profile(self, user_id: str, body: ProfileRequest) -> dict:
        self._active_creator(user_id)
        types = sorted({str(value) for value in body.collaboration_types})[:20]
        niches = sorted({str(value).strip()[:80] for value in body.niche_tags if str(value).strip()})[:30]
        regions = sorted({str(value).strip()[:80] for value in body.region_tags if str(value).strip()})[:20]
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_collaboration_profiles
                   (user_id,opt_in,display_title,collaboration_types_json,niche_tags_json,region_tags_json,availability_note,updated_at)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   opt_in=excluded.opt_in,display_title=excluded.display_title,collaboration_types_json=excluded.collaboration_types_json,
                   niche_tags_json=excluded.niche_tags_json,region_tags_json=excluded.region_tags_json,
                   availability_note=excluded.availability_note,updated_at=excluded.updated_at""",
                (user_id, int(body.opt_in), " ".join(body.display_title.split())[:160], json.dumps(types), json.dumps(niches), json.dumps(regions), " ".join(body.availability_note.split())[:1200], _now()),
            )
            row = con.execute("SELECT * FROM esp_collaboration_profiles WHERE user_id=?", (user_id,)).fetchone()
        return self._decode_profile(row)

    def profiles(self, viewer_user_id: str) -> list[dict]:
        self._active_creator(viewer_user_id)
        with self._connect() as con:
            rows = con.execute(
                """SELECT p.*,u.display_name,e.tiktok_handle,e.region,n.niche FROM esp_collaboration_profiles p
                   JOIN users u ON u.id=p.user_id JOIN esp_memberships e ON e.user_id=p.user_id
                   LEFT JOIN esp_niche_profiles n ON n.user_id=p.user_id
                   WHERE p.opt_in=1 AND e.status IN ('active','owner') AND (e.roles IN ('creator','both') OR e.status='owner')
                   ORDER BY p.updated_at DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = self._decode_profile(row)
            item.pop("availability_note", None) if item["user_id"] != viewer_user_id else None
            result.append(item)
        return result

    def propose(self, user_id: str, body: ProposalRequest) -> dict:
        self._active_creator(user_id)
        invited = self._active_creator(body.invited_creator_user_id)
        if user_id == body.invited_creator_user_id:
            raise ValueError("Choose another ESP creator to collaborate with")
        with self._connect() as con:
            profile = con.execute("SELECT opt_in FROM esp_collaboration_profiles WHERE user_id=?", (body.invited_creator_user_id,)).fetchone()
            if profile is None or not bool(profile["opt_in"]):
                raise PermissionError("This creator is not currently opted into collaboration matching")
            event_id = uuid4().hex
            now = _now()
            con.execute(
                """INSERT INTO esp_collaboration_events
                   (id,proposing_creator_user_id,invited_creator_user_id,proposed_by_user_id,kind,title,starts_at,notes,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?, 'proposed',?,?)""",
                (event_id, user_id, invited["id"], user_id, body.kind, " ".join(body.title.split())[:220], body.starts_at, " ".join(body.notes.split())[:2000], now, now),
            )
        return self.get_event(event_id, user_id)

    def get_event(self, event_id: str, viewer_user_id: str, *, owner: bool = False) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_collaboration_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        item = dict(row)
        if not owner and viewer_user_id not in {item["proposing_creator_user_id"], item["invited_creator_user_id"]}:
            raise PermissionError("This collaboration is not assigned to your account")
        return item

    def events(self, user_id: str, *, owner: bool = False) -> list[dict]:
        with self._connect() as con:
            if owner:
                rows = con.execute("SELECT * FROM esp_collaboration_events ORDER BY created_at DESC LIMIT 500").fetchall()
            else:
                rows = con.execute(
                    """SELECT * FROM esp_collaboration_events WHERE proposing_creator_user_id=? OR invited_creator_user_id=?
                       ORDER BY COALESCE(starts_at,created_at) DESC LIMIT 200""",
                    (user_id, user_id),
                ).fetchall()
        return [dict(row) for row in rows]

    def set_status(self, event_id: str, actor_user_id: str, status: str, note: str = "", *, owner: bool = False) -> dict:
        event = self.get_event(event_id, actor_user_id, owner=owner)
        current = event["status"]
        if status == "accepted" and not owner and actor_user_id != event["invited_creator_user_id"]:
            raise PermissionError("Only the invited creator can accept this collaboration")
        if status == "declined" and not owner and actor_user_id != event["invited_creator_user_id"]:
            raise PermissionError("Only the invited creator can decline this collaboration")
        if status == "no_show" and not owner:
            raise PermissionError("No-show status requires owner review")
        if current in {"completed", "declined", "cancelled", "no_show"} and not owner:
            raise PermissionError("This collaboration is already closed")
        completed_at = _now() if status in {"completed", "no_show"} else None
        with self._connect() as con:
            con.execute(
                "UPDATE esp_collaboration_events SET status=?,status_note=?,updated_at=?,completed_at=? WHERE id=?",
                (status, " ".join((note or "").split())[:2000], _now(), completed_at, event_id),
            )
        return self.get_event(event_id, actor_user_id, owner=owner)


store = CollaborationStore()


def _context(request: Request):
    return require_esp_hub_member(request)


@router.get("/command-center/api/collaborations/profiles")
def collaboration_profiles(request: Request):
    member, membership = _context(request)
    if "creator" not in _roles(membership) and membership.get("status") != "owner":
        raise HTTPException(403, "Creator role required for collaboration matching")
    try:
        return {"profiles": store.profiles(member.user_id), "opt_in_only": True, "public_directory": False}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.put("/command-center/api/collaborations/profile")
def update_collaboration_profile(body: ProfileRequest, request: Request):
    member, membership = _context(request)
    if "creator" not in _roles(membership) and membership.get("status") != "owner":
        raise HTTPException(403, "Creator role required")
    try:
        return {"profile": store.set_profile(member.user_id, body)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/command-center/api/collaborations/events")
def collaboration_events(request: Request):
    member, membership = _context(request)
    return {"events": store.events(member.user_id, owner=membership.get("status") == "owner")}


@router.post("/command-center/api/collaborations/events")
def propose_collaboration(body: ProposalRequest, request: Request):
    member, membership = _context(request)
    if "creator" not in _roles(membership) and membership.get("status") != "owner":
        raise HTTPException(403, "Creator role required to propose a collaboration")
    try:
        return {"event": store.propose(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.patch("/command-center/api/collaborations/events/{event_id}")
def update_collaboration_event(event_id: str, body: EventStatusRequest, request: Request):
    member, membership = _context(request)
    try:
        return {"event": store.set_status(event_id, member.user_id, body.status, body.note, owner=membership.get("status") == "owner")}
    except KeyError as exc:
        raise HTTPException(404, "Collaboration not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/command-center/collaborations", response_class=HTMLResponse, include_in_schema=False)
def collaboration_page(request: Request):
    member, membership = _context(request)
    owner = membership.get("status") == "owner"
    events = store.events(member.user_id, owner=owner)
    rows = "".join(
        f"<tr><td>{escape(e['kind'].replace('_',' ').title())}</td><td>{escape(e['title'])}</td><td>{escape(str(e.get('starts_at') or 'TBC'))}</td><td>{escape(e['status'].replace('_',' ').title())}</td></tr>"
        for e in events
    ) or "<tr><td colspan='4' class='muted'>No collaboration events yet.</td></tr>"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Collaborations</title><style>:root{{--line:#ffffff1e;--gold:#f1c86f;--muted:#c3bfd0}}*{{box-sizing:border-box}}body{{margin:0;background:#07050c;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit}}.wrap{{width:min(1100px,calc(100% - 28px));margin:auto;padding:36px 0}}.eyebrow{{color:var(--gold);font-size:.7rem;font-weight:950;text-transform:uppercase;letter-spacing:.14em}}h1{{font-size:clamp(2.7rem,7vw,5.2rem);letter-spacing:-.06em;margin:.15em 0}}p,.muted{{color:var(--muted);line-height:1.55}}.card{{border:1px solid var(--line);border-radius:16px;padding:14px;background:#15101e;margin:10px 0}}.btn{{display:inline-block;border:1px solid var(--line);border-radius:9px;padding:8px 10px;text-decoration:none;color:#fff}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left}}</style></head><body><main class='wrap'><div class='eyebrow'>Elevate Souls Productions · Opt-in Growth</div><h1>Collaborations, Battles & Events</h1><p>Private ESP opt-in matching. No creator is placed in the matching directory unless they opt in, and proposals require explicit acceptance.</p><p><a class='btn' href='/command-center/member-hub'>Member Hub</a></p><section class='card'><h2>My events</h2><table><thead><tr><th>Type</th><th>Title</th><th>Start</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "CollaborationStore"]
