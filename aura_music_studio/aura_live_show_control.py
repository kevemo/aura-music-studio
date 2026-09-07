from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Show Builder"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
MAX_SEGMENTS_PER_PLAN = 100
ORDINAL_TEMP_OFFSET = MAX_SEGMENTS_PER_PLAN + 1000
PLAN_STATUSES = {"draft", "ready", "archived"}
SEGMENT_TYPES = {"intro", "talk", "game", "battle", "music", "q_and_a", "break", "outro", "custom"}
EMERGENCY_MODES = {"normal", "automation_pause", "safe_hold"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_show_plans (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(status IN ('draft','ready','archived'))
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_plans_user_updated
                ON live_show_plans(user_id,updated_at DESC);
            CREATE TABLE IF NOT EXISTS live_show_segments (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                title TEXT NOT NULL,
                segment_type TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                scene_name TEXT,
                cue_label TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES live_show_plans(id) ON DELETE CASCADE,
                UNIQUE(plan_id,ordinal),
                CHECK(duration_seconds BETWEEN 15 AND 14400)
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_segments_plan_order
                ON live_show_segments(plan_id,ordinal);
            CREATE TABLE IF NOT EXISTS live_overlay_emergency_state (
                user_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'normal',
                reason TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                CHECK(mode IN ('normal','automation_pause','safe_hold'))
            );
            CREATE TABLE IF NOT EXISTS live_overlay_emergency_commands (
                user_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                requested_mode TEXT NOT NULL,
                previous_mode TEXT NOT NULL,
                resulting_revision INTEGER NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,command_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_emergency_commands_user_time
                ON live_overlay_emergency_commands(user_id,created_at DESC);
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def _required_text(value: str, label: str, maximum: int) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise HTTPException(422, f"{label} is required")
    if len(cleaned) > maximum:
        raise HTTPException(422, f"{label} is too long")
    return cleaned


def _optional_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise HTTPException(422, "LIVE show text field is too long")
    return cleaned


def _plan_row(con: sqlite3.Connection, user_id: str, plan_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM live_show_plans WHERE id=? AND user_id=?", (plan_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "LIVE show plan not found")
    return row


def _segment_payload(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "ordinal": int(row["ordinal"]),
        "title": row["title"],
        "segment_type": row["segment_type"],
        "duration_seconds": int(row["duration_seconds"]),
        "scene_name": row["scene_name"],
        "cue_label": row["cue_label"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _plan_payload(con: sqlite3.Connection, row: sqlite3.Row, *, with_segments: bool = True) -> dict:
    payload = {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if with_segments:
        rows = con.execute(
            "SELECT * FROM live_show_segments WHERE plan_id=? AND user_id=? ORDER BY ordinal,id",
            (row["id"], row["user_id"]),
        ).fetchall()
        payload["segments"] = [_segment_payload(item) for item in rows]
    return payload


def emergency_mode_from_connection(con: sqlite3.Connection, user_id: str) -> str:
    """Read Aura-local emergency mode from an existing transaction; missing legacy schema fails normal."""
    try:
        row = con.execute("SELECT mode FROM live_overlay_emergency_state WHERE user_id=?", (user_id,)).fetchone()
    except sqlite3.OperationalError:
        return "normal"
    mode = str(row["mode"]) if row else "normal"
    return mode if mode in EMERGENCY_MODES else "normal"


def _emergency_payload(mode: str, reason: str = "", revision: int = 0, updated_at: str | None = None) -> dict:
    mode = mode if mode in EMERGENCY_MODES else "normal"
    return {
        "mode": mode,
        "reason": reason,
        "revision": int(revision),
        "updated_at": updated_at,
        "automation_suppressed": mode in {"automation_pause", "safe_hold"},
        "overlay_safe_hold": mode == "safe_hold",
        "provider_write_authority": False,
        "provider_live_ended": False,
        "guardian_safeguarding_escalation_preserved": True,
        "provider_limitation": "Aura emergency controls affect Command Center LIVE automation and overlays only. Ending or controlling the provider LIVE remains manual unless a separately approved provider capability proves that authority.",
    }


def emergency_snapshot(user_id: str) -> dict:
    """Return server-authoritative Aura-local emergency state without claiming provider authority."""
    with _connect() as con:
        row = con.execute(
            "SELECT mode,reason,revision,updated_at FROM live_overlay_emergency_state WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return _emergency_payload("normal")
    return _emergency_payload(str(row["mode"]), str(row["reason"]), int(row["revision"]), str(row["updated_at"]))


def _move_segment_ordinal(con: sqlite3.Connection, plan_id: str, segment_id: str, current: int, wanted: int) -> None:
    """Reorder without transient UNIQUE(plan_id, ordinal) collisions in SQLite."""
    if wanted == current:
        return
    con.execute("UPDATE live_show_segments SET ordinal=-1 WHERE id=? AND plan_id=?", (segment_id, plan_id))
    if wanted < current:
        con.execute(
            "UPDATE live_show_segments SET ordinal=ordinal+? WHERE plan_id=? AND ordinal>=? AND ordinal<?",
            (ORDINAL_TEMP_OFFSET, plan_id, wanted, current),
        )
        con.execute(
            "UPDATE live_show_segments SET ordinal=ordinal-? WHERE plan_id=? AND ordinal>=? AND ordinal<?",
            (ORDINAL_TEMP_OFFSET - 1, plan_id, ORDINAL_TEMP_OFFSET + wanted, ORDINAL_TEMP_OFFSET + current),
        )
    else:
        con.execute(
            "UPDATE live_show_segments SET ordinal=ordinal+? WHERE plan_id=? AND ordinal>? AND ordinal<=?",
            (ORDINAL_TEMP_OFFSET, plan_id, current, wanted),
        )
        con.execute(
            "UPDATE live_show_segments SET ordinal=ordinal-? WHERE plan_id=? AND ordinal>? AND ordinal<=?",
            (ORDINAL_TEMP_OFFSET + 1, plan_id, ORDINAL_TEMP_OFFSET + current, ORDINAL_TEMP_OFFSET + wanted),
        )
    con.execute("UPDATE live_show_segments SET ordinal=? WHERE id=? AND plan_id=?", (wanted, segment_id, plan_id))


def _close_deleted_ordinal_gap(con: sqlite3.Connection, plan_id: str, deleted_ordinal: int) -> None:
    con.execute(
        "UPDATE live_show_segments SET ordinal=ordinal+? WHERE plan_id=? AND ordinal>?",
        (ORDINAL_TEMP_OFFSET, plan_id, deleted_ordinal),
    )
    con.execute(
        "UPDATE live_show_segments SET ordinal=ordinal-? WHERE plan_id=? AND ordinal>?",
        (ORDINAL_TEMP_OFFSET + 1, plan_id, ORDINAL_TEMP_OFFSET + deleted_ordinal),
    )


class ShowPlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=800)


class ShowPlanUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=800)
    status: Literal["draft", "ready", "archived"] | None = None


class ShowSegmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    segment_type: Literal["intro", "talk", "game", "battle", "music", "q_and_a", "break", "outro", "custom"] = "talk"
    duration_seconds: int = Field(default=300, ge=15, le=14400)
    scene_name: str | None = Field(default=None, max_length=120)
    cue_label: str | None = Field(default=None, max_length=160)


class ShowSegmentUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    segment_type: Literal["intro", "talk", "game", "battle", "music", "q_and_a", "break", "outro", "custom"] | None = None
    duration_seconds: int | None = Field(default=None, ge=15, le=14400)
    scene_name: str | None = Field(default=None, max_length=120)
    cue_label: str | None = Field(default=None, max_length=160)
    ordinal: int | None = Field(default=None, ge=1, le=100)


class EmergencyCommand(BaseModel):
    command_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    mode: Literal["normal", "automation_pause", "safe_hold"]
    reason: str = Field(default="", max_length=300)
    expected_revision: int = Field(ge=0)


@router.get("/api/live-show/plans")
def list_show_plans(request: Request):
    member = _member(request)
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM live_show_plans WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",
            (member.user_id,),
        ).fetchall()
        return {"plans": [_plan_payload(con, row, with_segments=False) for row in rows]}


@router.post("/api/live-show/plans")
def create_show_plan(body: ShowPlanCreate, request: Request):
    member = _member(request)
    now = _now()
    plan_id = _new_id("show")
    with _connect() as con:
        con.execute(
            "INSERT INTO live_show_plans(id,user_id,title,description,status,revision,created_at,updated_at) VALUES(?,?,?,?, 'draft',1,?,?)",
            (plan_id, member.user_id, _required_text(body.title, "Show title", 120), body.description.strip(), now, now),
        )
        return _plan_payload(con, _plan_row(con, member.user_id, plan_id))


@router.get("/api/live-show/plans/{plan_id}")
def get_show_plan(plan_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        return _plan_payload(con, _plan_row(con, member.user_id, plan_id))


@router.patch("/api/live-show/plans/{plan_id}")
def update_show_plan(plan_id: str, body: ShowPlanUpdate, request: Request):
    member = _member(request)
    values = body.model_dump(exclude_none=True)
    expected = int(values.pop("expected_revision"))
    if "title" in values:
        values["title"] = _required_text(values["title"], "Show title", 120)
    if "description" in values:
        values["description"] = str(values["description"]).strip()
    if "status" in values and values["status"] not in PLAN_STATUSES:
        raise HTTPException(422, "Unsupported show-plan status")
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = _plan_row(con, member.user_id, plan_id)
        if int(row["revision"]) != expected:
            raise HTTPException(409, "LIVE show plan changed; reload before saving")
        if values:
            values["updated_at"] = _now()
            assignments = ",".join(f"{key}=?" for key in values)
            result = con.execute(
                f"UPDATE live_show_plans SET {assignments},revision=revision+1 WHERE id=? AND user_id=? AND revision=?",
                [*values.values(), plan_id, member.user_id, expected],
            )
            if result.rowcount != 1:
                raise HTTPException(409, "LIVE show plan changed; reload before saving")
        return _plan_payload(con, _plan_row(con, member.user_id, plan_id))


@router.delete("/api/live-show/plans/{plan_id}")
def delete_show_plan(plan_id: str, request: Request, expected_revision: int):
    member = _member(request)
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = _plan_row(con, member.user_id, plan_id)
        if int(row["revision"]) != int(expected_revision):
            raise HTTPException(409, "LIVE show plan changed; reload before deleting")
        con.execute("DELETE FROM live_show_plans WHERE id=? AND user_id=?", (plan_id, member.user_id))
    return {"deleted": True, "plan_id": plan_id}


@router.post("/api/live-show/plans/{plan_id}/segments")
def add_show_segment(plan_id: str, body: ShowSegmentCreate, request: Request, expected_revision: int):
    member = _member(request)
    now = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        plan = _plan_row(con, member.user_id, plan_id)
        if int(plan["revision"]) != int(expected_revision):
            raise HTTPException(409, "LIVE show plan changed; reload before adding a segment")
        count = int(con.execute("SELECT COUNT(*) FROM live_show_segments WHERE plan_id=?", (plan_id,)).fetchone()[0])
        if count >= MAX_SEGMENTS_PER_PLAN:
            raise HTTPException(409, "LIVE show plan already has the maximum number of segments")
        segment_id = _new_id("segment")
        con.execute(
            """INSERT INTO live_show_segments(
                   id,plan_id,user_id,ordinal,title,segment_type,duration_seconds,scene_name,cue_label,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                segment_id,
                plan_id,
                member.user_id,
                count + 1,
                _required_text(body.title, "Segment title", 120),
                body.segment_type,
                int(body.duration_seconds),
                _optional_text(body.scene_name, 120),
                _optional_text(body.cue_label, 160),
                now,
                now,
            ),
        )
        con.execute(
            "UPDATE live_show_plans SET revision=revision+1,updated_at=? WHERE id=? AND user_id=?",
            (now, plan_id, member.user_id),
        )
        return _plan_payload(con, _plan_row(con, member.user_id, plan_id))


@router.patch("/api/live-show/plans/{plan_id}/segments/{segment_id}")
def update_show_segment(plan_id: str, segment_id: str, body: ShowSegmentUpdate, request: Request):
    member = _member(request)
    values = body.model_dump(exclude_none=True)
    expected = int(values.pop("expected_revision"))
    if "title" in values:
        values["title"] = _required_text(values["title"], "Segment title", 120)
    if "scene_name" in values:
        values["scene_name"] = _optional_text(values["scene_name"], 120)
    if "cue_label" in values:
        values["cue_label"] = _optional_text(values["cue_label"], 160)
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        plan = _plan_row(con, member.user_id, plan_id)
        if int(plan["revision"]) != expected:
            raise HTTPException(409, "LIVE show plan changed; reload before editing a segment")
        row = con.execute(
            "SELECT ordinal FROM live_show_segments WHERE id=? AND plan_id=? AND user_id=?",
            (segment_id, plan_id, member.user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "LIVE show segment not found")
        if "ordinal" in values:
            wanted = int(values.pop("ordinal"))
            count = int(con.execute("SELECT COUNT(*) FROM live_show_segments WHERE plan_id=?", (plan_id,)).fetchone()[0])
            wanted = min(max(1, wanted), count)
            _move_segment_ordinal(con, plan_id, segment_id, int(row["ordinal"]), wanted)
        if values:
            values["updated_at"] = _now()
            assignments = ",".join(f"{key}=?" for key in values)
            con.execute(
                f"UPDATE live_show_segments SET {assignments} WHERE id=? AND plan_id=? AND user_id=?",
                [*values.values(), segment_id, plan_id, member.user_id],
            )
        changed = con.execute(
            "UPDATE live_show_plans SET revision=revision+1,updated_at=? WHERE id=? AND user_id=? AND revision=?",
            (_now(), plan_id, member.user_id, expected),
        )
        if changed.rowcount != 1:
            raise HTTPException(409, "LIVE show plan changed; reload before editing a segment")
        return _plan_payload(con, _plan_row(con, member.user_id, plan_id))


@router.delete("/api/live-show/plans/{plan_id}/segments/{segment_id}")
def delete_show_segment(plan_id: str, segment_id: str, request: Request, expected_revision: int):
    member = _member(request)
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        plan = _plan_row(con, member.user_id, plan_id)
        if int(plan["revision"]) != int(expected_revision):
            raise HTTPException(409, "LIVE show plan changed; reload before deleting a segment")
        row = con.execute(
            "SELECT ordinal FROM live_show_segments WHERE id=? AND plan_id=? AND user_id=?",
            (segment_id, plan_id, member.user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "LIVE show segment not found")
        ordinal = int(row["ordinal"])
        con.execute("DELETE FROM live_show_segments WHERE id=? AND plan_id=? AND user_id=?", (segment_id, plan_id, member.user_id))
        _close_deleted_ordinal_gap(con, plan_id, ordinal)
        con.execute(
            "UPDATE live_show_plans SET revision=revision+1,updated_at=? WHERE id=? AND user_id=?",
            (_now(), plan_id, member.user_id),
        )
        return _plan_payload(con, _plan_row(con, member.user_id, plan_id))


@router.get("/api/live-show/emergency")
def get_emergency_state(request: Request):
    member = _member(request)
    return JSONResponse(
        emergency_snapshot(member.user_id),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-show/emergency")
def change_emergency_state(body: EmergencyCommand, request: Request):
    member = _member(request)
    actor = f"member:{member.user_id}"
    now = _now()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        prior = con.execute(
            "SELECT requested_mode,resulting_revision FROM live_overlay_emergency_commands WHERE user_id=? AND command_id=?",
            (member.user_id, body.command_id),
        ).fetchone()
        if prior:
            if str(prior["requested_mode"]) != body.mode:
                raise HTTPException(409, "Emergency command ID was already used for a different mode")
            state = con.execute(
                "SELECT mode,reason,revision,updated_at FROM live_overlay_emergency_state WHERE user_id=?",
                (member.user_id,),
            ).fetchone()
            snapshot = _emergency_payload(
                str(state["mode"]) if state else "normal",
                str(state["reason"]) if state else "",
                int(state["revision"]) if state else 0,
                str(state["updated_at"]) if state else None,
            )
            snapshot["duplicate_command"] = True
            return snapshot
        current = con.execute(
            "SELECT mode,revision FROM live_overlay_emergency_state WHERE user_id=?",
            (member.user_id,),
        ).fetchone()
        previous_mode = str(current["mode"]) if current else "normal"
        revision = int(current["revision"]) if current else 0
        if revision != int(body.expected_revision):
            raise HTTPException(409, "Emergency state changed; reload before issuing another command")
        next_revision = revision + 1
        reason = body.reason.strip()
        con.execute(
            """INSERT INTO live_overlay_emergency_state(user_id,mode,reason,revision,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,reason=excluded.reason,revision=excluded.revision,updated_at=excluded.updated_at""",
            (member.user_id, body.mode, reason, next_revision, now),
        )
        con.execute(
            """INSERT INTO live_overlay_emergency_commands(
                   user_id,command_id,requested_mode,previous_mode,resulting_revision,actor,reason,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (member.user_id, body.command_id, body.mode, previous_mode, next_revision, actor, reason, now),
        )
        con.execute(
            """DELETE FROM live_overlay_emergency_commands
               WHERE user_id=? AND rowid NOT IN (
                   SELECT rowid FROM live_overlay_emergency_commands WHERE user_id=? ORDER BY created_at DESC LIMIT 500
               )""",
            (member.user_id, member.user_id),
        )
    snapshot = emergency_snapshot(member.user_id)
    snapshot["duplicate_command"] = False
    return JSONResponse(snapshot, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})


@router.get("/live-overlay-studio/show-builder", response_class=HTMLResponse, include_in_schema=False)
def show_builder_page(request: Request):
    member = _member(request)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    product = escape(PRODUCT_FULL_NAME)
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow"><title>Creator Show Builder — __PRODUCT__</title><style>
:root{--bg:#070812;--card:#111526;--line:#ffffff20;--muted:#bdc6d8;--gold:#efc96b;--violet:#9b72ff;--danger:#ff8ca6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:30px 0 70px}h1{font-size:clamp(2.5rem,6vw,4.8rem);margin:.12em 0}.grid{display:grid;grid-template-columns:.78fr 1.22fr;gap:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:14px}.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted);line-height:1.55}button,.btn,input,select{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px}.btn{text-decoration:none;display:inline-block}button,.btn{cursor:pointer;font-weight:850}.primary{background:linear-gradient(120deg,var(--gold),var(--violet));color:#160b22;border:0}.danger{border-color:var(--danger);color:#ffdce4}input,select{width:100%}label{display:block;font-weight:800;margin:10px 0 5px}.plan{padding:12px;border:1px solid #ffffff16;border-radius:12px;margin:8px 0;cursor:pointer}.plan.active{border-color:var(--gold);background:#efc96b12}.segment{border:1px solid #ffffff16;border-radius:12px;padding:12px;margin:9px 0}.badge{display:inline-block;border:1px solid #ffffff25;border-radius:999px;padding:5px 8px;font-size:.78rem}.emergency{border-color:#ff8ca655}@media(max-width:820px){.grid{grid-template-columns:1fr}}</style></head><body><main class="wrap"><p><a class="btn" href="/live-overlay-studio">← Overlay Studio</a> <a class="btn" href="/live-overlay-studio/prompter">Open private Auto Cue</a> <a class="btn" href="/live-guardian">LIVE Guardian</a></p><div style="color:var(--gold);font-weight:900">Powered by Aura AI · Creator LIVE Production</div><h1>Creator Show Builder</h1><p class="muted">Welcome __DISPLAY_NAME__. Build a structured run-of-show with segments, timings, scene labels and private cue labels. <b>Your spoken Auto Cue script is deliberately not stored here.</b> Script text remains browser-local inside the private Auto Cue Prompter and is never written to this database, event feed, connector or analytics.</p><section class="grid"><aside><div class="card"><h2>Your show plans</h2><div id="plans" class="muted">Loading…</div><hr style="border:0;border-top:1px solid #ffffff18"><label>New show title</label><input id="newTitle" maxlength="120" placeholder="Friday Night Creator Show"><button id="create" class="primary" style="margin-top:10px">Create show plan</button></div><div class="card emergency"><h2>Emergency LIVE controls</h2><p id="emergencyState" class="muted">Loading state…</p><label>Reason / operator note</label><input id="reason" maxlength="300" placeholder="Optional operational reason"><div class="row" style="margin-top:10px"><button data-mode="automation_pause">Pause Aura automations</button><button data-mode="safe_hold" class="danger">Safe Hold</button><button data-mode="normal">Resume Aura</button></div><p class="muted"><b>Scope:</b> these controls govern Aura Command Center automation/overlay behavior only. They do not claim to end, mute, kick from or otherwise control TikTok LIVE. Guardian safeguarding evidence, review and serious-risk escalation stay active.</p></div></aside><section><div class="card"><h2 id="editorTitle">Select a show plan</h2><div id="editor" class="muted">Create or select a plan to edit its run-of-show.</div></div><div class="card"><h2>Private script handoff</h2><p class="muted">Use the <b>cue label</b> on each segment as a private reminder such as “Opening welcome” or “Sponsor CTA”. Put the actual spoken wording only in Auto Cue. This prevents show scripts from entering server logs, databases, overlays or provider payloads.</p><a class="btn primary" href="/live-overlay-studio/prompter">Open private Auto Cue Prompter</a></div></section></section></main><script>
const $=id=>document.getElementById(id);let current=null,emergency={revision:0,mode:'normal'};const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');async function api(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Request failed');return d}async function loadPlans(selectId){const d=await api('/api/live-show/plans');$('plans').innerHTML=d.plans.length?d.plans.map(p=>`<div class="plan ${current?.id===p.id?'active':''}" data-plan="${esc(p.id)}"><b>${esc(p.title)}</b><br><span class="badge">${esc(p.status)}</span> <span class="muted">rev ${p.revision}</span></div>`).join(''):'No show plans yet.';document.querySelectorAll('[data-plan]').forEach(x=>x.onclick=()=>openPlan(x.dataset.plan));if(selectId)await openPlan(selectId)}async function openPlan(id){current=await api('/api/live-show/plans/'+encodeURIComponent(id));$('editorTitle').textContent=current.title;renderEditor();await loadPlans()}function renderEditor(){if(!current)return;const segs=current.segments||[];$('editor').className='';$('editor').innerHTML=`<div class="row"><span class="badge">${esc(current.status)}</span><span class="muted">Revision ${current.revision}</span></div><label>Description</label><input id="description" maxlength="800" value="${esc(current.description)}"><div class="row" style="margin-top:10px"><button id="savePlan">Save description</button><button id="ready">${current.status==='ready'?'Return to draft':'Mark ready'}</button><button id="deletePlan" class="danger">Delete plan</button></div><h3>Run of show</h3><div id="segments">${segs.length?segs.map(s=>`<div class="segment"><div class="row"><b>${s.ordinal}. ${esc(s.title)}</b><span class="badge">${esc(s.segment_type)}</span><span class="muted">${Math.round(s.duration_seconds/60)} min</span></div><div class="muted">Scene: ${esc(s.scene_name||'—')} · Private cue label: ${esc(s.cue_label||'—')}</div><div class="row" style="margin-top:8px"><button data-up="${esc(s.id)}">Move up</button><button data-down="${esc(s.id)}">Move down</button><button data-del="${esc(s.id)}" class="danger">Delete</button></div></div>`).join(''):'<p class="muted">No segments yet.</p>'}</div><h3>Add segment</h3><label>Segment title</label><input id="segTitle" maxlength="120" placeholder="Opening welcome"><div class="row"><div style="flex:1"><label>Type</label><select id="segType"><option>intro</option><option>talk</option><option>game</option><option>battle</option><option>music</option><option value="q_and_a">q & a</option><option>break</option><option>outro</option><option>custom</option></select></div><div style="flex:1"><label>Minutes</label><input id="segMins" type="number" min="1" max="240" value="5"></div></div><label>Scene label</label><input id="scene" maxlength="120" placeholder="Main camera"><label>Private cue label only — never script text</label><input id="cue" maxlength="160" placeholder="Opening welcome cue"><button id="addSeg" class="primary" style="margin-top:10px">Add segment</button>`;$('savePlan').onclick=()=>mutatePlan({description:$('description').value});$('ready').onclick=()=>mutatePlan({status:current.status==='ready'?'draft':'ready'});$('deletePlan').onclick=deletePlan;$('addSeg').onclick=addSegment;document.querySelectorAll('[data-del]').forEach(x=>x.onclick=()=>deleteSegment(x.dataset.del));document.querySelectorAll('[data-up]').forEach(x=>x.onclick=()=>moveSegment(x.dataset.up,-1));document.querySelectorAll('[data-down]').forEach(x=>x.onclick=()=>moveSegment(x.dataset.down,1))}async function mutatePlan(p){try{current=await api('/api/live-show/plans/'+encodeURIComponent(current.id),{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({expected_revision:current.revision,...p})});renderEditor();await loadPlans()}catch(e){alert(e.message);await openPlan(current.id)}}async function addSegment(){const title=$('segTitle').value.trim();if(!title)return alert('Add a segment title.');try{current=await api('/api/live-show/plans/'+encodeURIComponent(current.id)+'/segments?expected_revision='+current.revision,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title,segment_type:$('segType').value,duration_seconds:Math.max(15,Number($('segMins').value||5)*60),scene_name:$('scene').value||null,cue_label:$('cue').value||null})});renderEditor();await loadPlans()}catch(e){alert(e.message);await openPlan(current.id)}}async function deleteSegment(id){if(!confirm('Delete this show segment?'))return;try{current=await api('/api/live-show/plans/'+encodeURIComponent(current.id)+'/segments/'+encodeURIComponent(id)+'?expected_revision='+current.revision,{method:'DELETE'});renderEditor();await loadPlans()}catch(e){alert(e.message);await openPlan(current.id)}}async function moveSegment(id,delta){const s=current.segments.find(x=>x.id===id);if(!s)return;const wanted=Math.max(1,Math.min(current.segments.length,s.ordinal+delta));if(wanted===s.ordinal)return;try{current=await api('/api/live-show/plans/'+encodeURIComponent(current.id)+'/segments/'+encodeURIComponent(id),{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({expected_revision:current.revision,ordinal:wanted})});renderEditor();await loadPlans()}catch(e){alert(e.message);await openPlan(current.id)}}async function deletePlan(){if(!confirm('Delete this show plan and its segments?'))return;try{await api('/api/live-show/plans/'+encodeURIComponent(current.id)+'?expected_revision='+current.revision,{method:'DELETE'});current=null;$('editorTitle').textContent='Select a show plan';$('editor').className='muted';$('editor').textContent='Create or select a plan to edit its run-of-show.';await loadPlans()}catch(e){alert(e.message)}}$('create').onclick=async()=>{const title=$('newTitle').value.trim();if(!title)return;try{const p=await api('/api/live-show/plans',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title})});$('newTitle').value='';await loadPlans(p.id)}catch(e){alert(e.message)}};async function loadEmergency(){emergency=await api('/api/live-show/emergency');$('emergencyState').innerHTML=`Aura mode: <b>${esc(emergency.mode.replaceAll('_',' '))}</b> · rev ${emergency.revision}<br><span class="muted">Provider write authority: No · Guardian serious-risk escalation preserved: Yes</span>`}document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=async()=>{const mode=b.dataset.mode;if(mode==='normal'&&!confirm('Resume Aura LIVE automations and overlay reactions?'))return;if(mode!=='normal'&&!confirm('Apply '+mode.replaceAll('_',' ')+' to Aura LIVE controls?'))return;try{emergency=await api('/api/live-show/emergency',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command_id:'cmd_'+crypto.randomUUID().replaceAll('-',''),mode,reason:$('reason').value,expected_revision:emergency.revision})});await loadEmergency()}catch(e){alert(e.message);await loadEmergency()}});loadPlans().catch(e=>$('plans').textContent=e.message);loadEmergency().catch(e=>$('emergencyState').textContent=e.message);
</script></body></html>'''.replace("__PRODUCT__", product).replace("__DISPLAY_NAME__", display_name)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = [
    "router",
    "emergency_snapshot",
    "emergency_mode_from_connection",
    "ShowPlanCreate",
    "ShowPlanUpdate",
    "ShowSegmentCreate",
    "ShowSegmentUpdate",
    "EmergencyCommand",
]
