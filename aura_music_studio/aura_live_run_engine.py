from __future__ import annotations

import secrets
import sqlite3
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import aura_live_show_control as show_control
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Run Engine"])
RUN_STATUSES = {"running", "paused", "completed", "aborted"}
ACTIVE_RUN_STATUSES = {"running", "paused"}
RUN_ACTIONS = {"next", "previous", "jump", "pause", "resume", "complete", "abort"}


def _now() -> str:
    return show_control._now()


def _connect() -> sqlite3.Connection:
    return show_control._connect()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def _init_schema() -> None:
    show_control._init_schema()
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_show_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_revision INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                current_ordinal INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ended_at TEXT,
                CHECK(status IN ('running','paused','completed','aborted')),
                CHECK(current_ordinal >= 1)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_show_runs_one_active_per_user
                ON live_show_runs(user_id)
                WHERE status IN ('running','paused');
            CREATE INDEX IF NOT EXISTS idx_live_show_runs_user_started
                ON live_show_runs(user_id,started_at DESC);

            CREATE TABLE IF NOT EXISTS live_show_run_segments (
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                source_segment_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                title TEXT NOT NULL,
                segment_type TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                scene_name TEXT,
                cue_label TEXT,
                PRIMARY KEY(run_id,ordinal),
                UNIQUE(run_id,source_segment_id),
                FOREIGN KEY(run_id) REFERENCES live_show_runs(id) ON DELETE CASCADE,
                CHECK(ordinal >= 1),
                CHECK(duration_seconds BETWEEN 15 AND 14400)
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_run_segments_user_run
                ON live_show_run_segments(user_id,run_id,ordinal);

            CREATE TABLE IF NOT EXISTS live_show_run_start_commands (
                user_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,command_id),
                FOREIGN KEY(run_id) REFERENCES live_show_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS live_show_run_commands (
                user_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                action TEXT NOT NULL,
                requested_ordinal INTEGER,
                previous_status TEXT NOT NULL,
                previous_ordinal INTEGER NOT NULL,
                resulting_status TEXT NOT NULL,
                resulting_ordinal INTEGER NOT NULL,
                resulting_revision INTEGER NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,run_id,command_id),
                FOREIGN KEY(run_id) REFERENCES live_show_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_run_commands_user_time
                ON live_show_run_commands(user_id,created_at DESC);
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _run_row(con: sqlite3.Connection, user_id: str, run_id: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM live_show_runs WHERE id=? AND user_id=?",
        (run_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "LIVE show run not found")
    return row


def _segments(con: sqlite3.Connection, user_id: str, run_id: str) -> list[dict]:
    rows = con.execute(
        """SELECT source_segment_id,ordinal,title,segment_type,duration_seconds,scene_name,cue_label
           FROM live_show_run_segments
           WHERE run_id=? AND user_id=?
           ORDER BY ordinal""",
        (run_id, user_id),
    ).fetchall()
    return [
        {
            "source_segment_id": row["source_segment_id"],
            "ordinal": int(row["ordinal"]),
            "title": row["title"],
            "segment_type": row["segment_type"],
            "duration_seconds": int(row["duration_seconds"]),
            "scene_name": row["scene_name"],
            "cue_label": row["cue_label"],
        }
        for row in rows
    ]


def _run_payload(con: sqlite3.Connection, row: sqlite3.Row) -> dict:
    segments = _segments(con, str(row["user_id"]), str(row["id"]))
    current_ordinal = int(row["current_ordinal"])
    current = next((item for item in segments if item["ordinal"] == current_ordinal), None)
    emergency_mode = show_control.emergency_mode_from_connection(con, str(row["user_id"]))
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "plan_revision": int(row["plan_revision"]),
        "title": row["title"],
        "status": row["status"],
        "current_ordinal": current_ordinal,
        "current_segment": current,
        "segments": segments,
        "revision": int(row["revision"]),
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "ended_at": row["ended_at"],
        "emergency_mode": emergency_mode,
        "automation_suppressed": emergency_mode in {"automation_pause", "safe_hold"},
        "provider_write_authority": False,
        "provider_live_started": False,
        "provider_live_ended": False,
        "guardian_safeguarding_escalation_preserved": True,
        "provider_limitation": (
            "Aura LIVE Run Engine tracks the creator's Command Center run-of-show only. "
            "Starting, ending, muting, guest control, moderation, battles and other provider LIVE "
            "actions remain manual unless a separately approved provider capability proves that authority."
        ),
    }


class LiveRunStart(BaseModel):
    command_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    plan_id: str = Field(min_length=8, max_length=128)
    expected_plan_revision: int = Field(ge=1)


class LiveRunCommand(BaseModel):
    command_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    action: Literal["next", "previous", "jump", "pause", "resume", "complete", "abort"]
    expected_revision: int = Field(ge=1)
    ordinal: int | None = Field(default=None, ge=1, le=100)


def _active_row(con: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return con.execute(
        """SELECT * FROM live_show_runs
           WHERE user_id=? AND status IN ('running','paused')
           ORDER BY started_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()


@router.get("/api/live-show/runs/active")
def get_active_run(request: Request):
    member = _member(request)
    with _connect() as con:
        row = _active_row(con, member.user_id)
        payload = _run_payload(con, row) if row else None
    return JSONResponse(
        {"run": payload},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/api/live-show/runs/{run_id}")
def get_run(run_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        payload = _run_payload(con, _run_row(con, member.user_id, run_id))
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-show/runs")
def start_run(body: LiveRunStart, request: Request):
    member = _member(request)
    user_id = member.user_id
    now = _now()
    duplicate = False
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        prior = con.execute(
            """SELECT plan_id,run_id FROM live_show_run_start_commands
               WHERE user_id=? AND command_id=?""",
            (user_id, body.command_id),
        ).fetchone()
        if prior:
            if str(prior["plan_id"]) != body.plan_id:
                raise HTTPException(409, "LIVE run start command ID was already used for another plan")
            row = _run_row(con, user_id, str(prior["run_id"]))
            payload = _run_payload(con, row)
            duplicate = True
        else:
            active = _active_row(con, user_id)
            if active:
                raise HTTPException(
                    409,
                    f"A LIVE show run is already active ({active['id']}); finish or abort it before starting another",
                )
            plan = con.execute(
                "SELECT * FROM live_show_plans WHERE id=? AND user_id=?",
                (body.plan_id, user_id),
            ).fetchone()
            if not plan:
                raise HTTPException(404, "LIVE show plan not found")
            if int(plan["revision"]) != int(body.expected_plan_revision):
                raise HTTPException(409, "LIVE show plan changed; reload before starting")
            if str(plan["status"]) != "ready":
                raise HTTPException(409, "Only a ready LIVE show plan can be started")
            plan_segments = con.execute(
                """SELECT id,ordinal,title,segment_type,duration_seconds,scene_name,cue_label
                   FROM live_show_segments
                   WHERE plan_id=? AND user_id=?
                   ORDER BY ordinal""",
                (body.plan_id, user_id),
            ).fetchall()
            if not plan_segments:
                raise HTTPException(409, "LIVE show plan needs at least one segment before it can start")

            run_id = _new_id("run")
            con.execute(
                """INSERT INTO live_show_runs(
                       id,user_id,plan_id,plan_revision,title,status,current_ordinal,revision,
                       started_at,updated_at,ended_at
                   ) VALUES(?,?,?,?,?,'running',1,1,?,?,NULL)""",
                (
                    run_id,
                    user_id,
                    body.plan_id,
                    int(plan["revision"]),
                    str(plan["title"]),
                    now,
                    now,
                ),
            )
            con.executemany(
                """INSERT INTO live_show_run_segments(
                       run_id,user_id,source_segment_id,ordinal,title,segment_type,duration_seconds,
                       scene_name,cue_label
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        user_id,
                        str(segment["id"]),
                        int(segment["ordinal"]),
                        str(segment["title"]),
                        str(segment["segment_type"]),
                        int(segment["duration_seconds"]),
                        segment["scene_name"],
                        segment["cue_label"],
                    )
                    for segment in plan_segments
                ],
            )
            con.execute(
                """INSERT INTO live_show_run_start_commands(user_id,command_id,plan_id,run_id,created_at)
                   VALUES(?,?,?,?,?)""",
                (user_id, body.command_id, body.plan_id, run_id, now),
            )
            payload = _run_payload(con, _run_row(con, user_id, run_id))
    payload["duplicate_command"] = duplicate
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


def _resolve_transition(
    *,
    action: str,
    status: str,
    current_ordinal: int,
    segment_count: int,
    requested_ordinal: int | None,
) -> tuple[str, int, str | None]:
    if status not in ACTIVE_RUN_STATUSES:
        raise HTTPException(409, "LIVE show run is no longer active")

    next_status = status
    next_ordinal = current_ordinal
    ended_at: str | None = None

    if action == "pause":
        if status != "running":
            raise HTTPException(409, "LIVE show run is already paused")
        next_status = "paused"
    elif action == "resume":
        if status != "paused":
            raise HTTPException(409, "LIVE show run is not paused")
        next_status = "running"
    elif action == "next":
        if current_ordinal >= segment_count:
            raise HTTPException(409, "Already at the final segment; complete the show when ready")
        next_ordinal += 1
    elif action == "previous":
        if current_ordinal <= 1:
            raise HTTPException(409, "Already at the first segment")
        next_ordinal -= 1
    elif action == "jump":
        if requested_ordinal is None:
            raise HTTPException(422, "Jump requires a target ordinal")
        if requested_ordinal > segment_count:
            raise HTTPException(422, "Jump target is outside this LIVE show run")
        next_ordinal = requested_ordinal
    elif action in {"complete", "abort"}:
        next_status = "completed" if action == "complete" else "aborted"
        ended_at = _now()
    else:
        raise HTTPException(422, "Unsupported LIVE run action")

    return next_status, next_ordinal, ended_at


@router.post("/api/live-show/runs/{run_id}/commands")
def command_run(run_id: str, body: LiveRunCommand, request: Request):
    member = _member(request)
    user_id = member.user_id
    actor = f"member:{user_id}"
    now = _now()
    duplicate = False

    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = _run_row(con, user_id, run_id)
        prior = con.execute(
            """SELECT action,requested_ordinal FROM live_show_run_commands
               WHERE user_id=? AND run_id=? AND command_id=?""",
            (user_id, run_id, body.command_id),
        ).fetchone()
        requested_ordinal = int(body.ordinal) if body.ordinal is not None else None
        if prior:
            prior_ordinal = int(prior["requested_ordinal"]) if prior["requested_ordinal"] is not None else None
            if str(prior["action"]) != body.action or prior_ordinal != requested_ordinal:
                raise HTTPException(409, "LIVE run command ID was already used for a different action")
            payload = _run_payload(con, row)
            duplicate = True
        else:
            if int(row["revision"]) != int(body.expected_revision):
                raise HTTPException(409, "LIVE show run changed; reload before issuing another command")
            count = int(
                con.execute(
                    "SELECT COUNT(*) FROM live_show_run_segments WHERE run_id=? AND user_id=?",
                    (run_id, user_id),
                ).fetchone()[0]
            )
            next_status, next_ordinal, ended_at = _resolve_transition(
                action=body.action,
                status=str(row["status"]),
                current_ordinal=int(row["current_ordinal"]),
                segment_count=count,
                requested_ordinal=requested_ordinal,
            )
            next_revision = int(row["revision"]) + 1
            changed = con.execute(
                """UPDATE live_show_runs
                   SET status=?,current_ordinal=?,revision=?,updated_at=?,ended_at=?
                   WHERE id=? AND user_id=? AND revision=?""",
                (
                    next_status,
                    next_ordinal,
                    next_revision,
                    now,
                    ended_at,
                    run_id,
                    user_id,
                    int(body.expected_revision),
                ),
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "LIVE show run changed; reload before issuing another command")
            con.execute(
                """INSERT INTO live_show_run_commands(
                       user_id,run_id,command_id,action,requested_ordinal,previous_status,
                       previous_ordinal,resulting_status,resulting_ordinal,resulting_revision,
                       actor,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    run_id,
                    body.command_id,
                    body.action,
                    requested_ordinal,
                    str(row["status"]),
                    int(row["current_ordinal"]),
                    next_status,
                    next_ordinal,
                    next_revision,
                    actor,
                    now,
                ),
            )
            con.execute(
                """DELETE FROM live_show_run_commands
                   WHERE user_id=? AND rowid NOT IN (
                       SELECT rowid FROM live_show_run_commands
                       WHERE user_id=? ORDER BY created_at DESC LIMIT 1000
                   )""",
                (user_id, user_id),
            )
            payload = _run_payload(con, _run_row(con, user_id, run_id))

    payload["duplicate_command"] = duplicate
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay-studio/show-runner", response_class=HTMLResponse, include_in_schema=False)
def show_runner_page(request: Request):
    member = _member(request)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    product = escape(PRODUCT_FULL_NAME)
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow"><title>LIVE Show Runner — __PRODUCT__</title><style>
:root{--bg:#070812;--card:#111526;--line:#ffffff20;--muted:#bdc6d8;--gold:#efc96b;--violet:#9b72ff;--danger:#ff8ca6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1100px,calc(100% - 28px));margin:auto;padding:30px 0 70px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted);line-height:1.55}button,.btn,select{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px}.btn{text-decoration:none;display:inline-block}button,.btn{cursor:pointer;font-weight:850}.primary{background:linear-gradient(120deg,var(--gold),var(--violet));color:#160b22;border:0}.danger{border-color:var(--danger);color:#ffdce4}.segment{border:1px solid #ffffff16;border-radius:12px;padding:12px;margin:8px 0}.segment.current{border-color:var(--gold);background:#efc96b12}.badge{display:inline-block;border:1px solid #ffffff25;border-radius:999px;padding:5px 8px;font-size:.8rem}</style></head><body><main class="wrap"><p><a class="btn" href="/live-overlay-studio/show-builder">← Show Builder</a> <a class="btn" href="/live-overlay-studio/prompter">Private Auto Cue</a> <a class="btn" href="/live-guardian">LIVE Guardian</a></p><div style="color:var(--gold);font-weight:900">Powered by Aura AI · Creator LIVE Production</div><h1>LIVE Show Runner</h1><p class="muted">Welcome __DISPLAY_NAME__. Run a ready show plan from a durable Command Center state that survives refreshes and process restarts. This runner stores only show structure and cue labels. Spoken Auto Cue script text remains browser-local and is not stored here.</p><div id="app" class="card muted">Loading…</div><div class="card"><b>Provider truth:</b><p class="muted">Starting or completing a Show Runner session does not start or end TikTok LIVE and does not grant Aura provider moderation, battle, guest, mute or kick authority. Those controls remain manual unless a separately approved provider capability proves otherwise. LIVE Guardian safeguarding remains independent and preserved.</p></div></main><script>
const $=id=>document.getElementById(id);const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');let run=null,plans=[];async function api(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Request failed');return d}async function load(){const [a,p]=await Promise.all([api('/api/live-show/runs/active'),api('/api/live-show/plans')]);run=a.run;plans=(p.plans||[]).filter(x=>x.status==='ready');render()}function render(){if(run){const segs=run.segments||[];$('app').className='card';$('app').innerHTML=`<div class="row"><span class="badge">${esc(run.status)}</span><span class="badge">Revision ${run.revision}</span><span class="badge">Emergency: ${esc(run.emergency_mode)}</span></div><h2>${esc(run.title)}</h2><p class="muted">Current segment ${run.current_ordinal} of ${segs.length}. Aura automations suppressed: <b>${run.automation_suppressed?'Yes':'No'}</b>.</p><div>${segs.map(s=>`<div class="segment ${s.ordinal===run.current_ordinal?'current':''}"><b>${s.ordinal}. ${esc(s.title)}</b><div class="muted">${esc(s.segment_type)} · ${Math.round(s.duration_seconds/60)} min · Scene: ${esc(s.scene_name||'—')} · Cue: ${esc(s.cue_label||'—')}</div></div>`).join('')}</div><div class="row"><button data-action="previous">Previous</button><button data-action="next" class="primary">Next</button><button data-action="${run.status==='paused'?'resume':'pause'}">${run.status==='paused'?'Resume timer state':'Pause timer state'}</button><button data-action="complete">Complete runner</button><button data-action="abort" class="danger">Abort runner</button></div>`;document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>command(b.dataset.action));return}if(!plans.length){$('app').innerHTML='No ready show plans. Mark a plan ready in Show Builder first.';return}$('app').className='card';$('app').innerHTML=`<h2>Start a ready show</h2><select id="plan">${plans.map(p=>`<option value="${esc(p.id)}" data-rev="${p.revision}">${esc(p.title)} · rev ${p.revision}</option>`).join('')}</select><button id="start" class="primary" style="margin-top:10px">Start Show Runner</button><p class="muted">This starts the Aura run-of-show tracker only, not the provider LIVE.</p>`;$('start').onclick=start}async function start(){const el=$('plan'),opt=el.options[el.selectedIndex];if(!confirm('Start this Aura Show Runner session? This does not start the provider LIVE.'))return;try{run=await api('/api/live-show/runs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command_id:'start_'+crypto.randomUUID().replaceAll('-',''),plan_id:el.value,expected_plan_revision:Number(opt.dataset.rev)})});render()}catch(e){alert(e.message);await load()}}async function command(action){if((action==='complete'||action==='abort')&&!confirm(action==='complete'?'Complete this Show Runner session? This will not end the provider LIVE.':'Abort this Show Runner session? This will not end the provider LIVE.'))return;try{run=await api('/api/live-show/runs/'+encodeURIComponent(run.id)+'/commands',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command_id:'cmd_'+crypto.randomUUID().replaceAll('-',''),action,expected_revision:run.revision})});if(run.status==='completed'||run.status==='aborted'){run=null;await load()}else render()}catch(e){alert(e.message);await load()}}load().catch(e=>$('app').textContent=e.message);
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
    "LiveRunStart",
    "LiveRunCommand",
    "start_run",
    "command_run",
    "get_active_run",
    "get_run",
]
