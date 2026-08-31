from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import aura_live_run_engine as run_engine
from . import aura_live_show_control as show_control
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Runtime Intelligence"])
DRIFT_THRESHOLD_SECONDS = 120


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _utcnow().isoformat()


def _connect() -> sqlite3.Connection:
    return run_engine._connect()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _init_schema() -> None:
    run_engine._init_schema()
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_show_run_readiness (
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                cue_ready INTEGER NOT NULL DEFAULT 0,
                scene_ready INTEGER NOT NULL DEFAULT 0,
                assets_ready INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT,
                PRIMARY KEY(run_id,ordinal),
                FOREIGN KEY(run_id,ordinal) REFERENCES live_show_run_segments(run_id,ordinal) ON DELETE CASCADE,
                CHECK(cue_ready IN (0,1)),
                CHECK(scene_ready IN (0,1)),
                CHECK(assets_ready IN (0,1)),
                CHECK(revision >= 0)
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_run_readiness_user_run
                ON live_show_run_readiness(user_id,run_id,ordinal);

            CREATE TABLE IF NOT EXISTS live_show_run_readiness_commands (
                user_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                requested_cue_ready INTEGER,
                requested_scene_ready INTEGER,
                requested_assets_ready INTEGER,
                resulting_revision INTEGER NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,run_id,command_id),
                FOREIGN KEY(run_id,ordinal) REFERENCES live_show_run_segments(run_id,ordinal) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_run_readiness_commands_user_time
                ON live_show_run_readiness_commands(user_id,created_at DESC);
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _readiness_row(con: sqlite3.Connection, user_id: str, run_id: str, ordinal: int) -> dict:
    row = con.execute(
        """SELECT cue_ready,scene_ready,assets_ready,revision,updated_by,updated_at
           FROM live_show_run_readiness
           WHERE user_id=? AND run_id=? AND ordinal=?""",
        (user_id, run_id, ordinal),
    ).fetchone()
    if not row:
        return {
            "ordinal": ordinal,
            "cue_ready": False,
            "scene_ready": False,
            "assets_ready": False,
            "all_ready": False,
            "revision": 0,
            "updated_by": "",
            "updated_at": None,
        }
    cue_ready = bool(row["cue_ready"])
    scene_ready = bool(row["scene_ready"])
    assets_ready = bool(row["assets_ready"])
    return {
        "ordinal": ordinal,
        "cue_ready": cue_ready,
        "scene_ready": scene_ready,
        "assets_ready": assets_ready,
        "all_ready": cue_ready and scene_ready and assets_ready,
        "revision": int(row["revision"]),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def _command_rows(con: sqlite3.Connection, user_id: str, run_id: str) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT action,previous_status,previous_ordinal,resulting_status,resulting_ordinal,created_at
           FROM live_show_run_commands
           WHERE user_id=? AND run_id=?
           ORDER BY created_at,rowid""",
        (user_id, run_id),
    ).fetchall()


def _active_elapsed_at(run_started_at: datetime, commands: list[sqlite3.Row], at: datetime) -> float:
    if at <= run_started_at:
        return 0.0
    state = "running"
    cursor = run_started_at
    active_seconds = 0.0
    for command in commands:
        command_at = _parse_ts(command["created_at"])
        if command_at is None or command_at > at:
            break
        if command_at < cursor:
            continue
        if state == "running":
            active_seconds += (command_at - cursor).total_seconds()
        state = str(command["resulting_status"])
        cursor = command_at
    if cursor < at and state == "running":
        active_seconds += (at - cursor).total_seconds()
    return max(0.0, active_seconds)


def _segment_entered_at(
    run_started_at: datetime,
    commands: list[sqlite3.Row],
    current_ordinal: int,
    horizon: datetime,
) -> datetime:
    entered = run_started_at
    for command in commands:
        command_at = _parse_ts(command["created_at"])
        if command_at is None or command_at > horizon:
            break
        if (
            int(command["resulting_ordinal"]) == current_ordinal
            and int(command["previous_ordinal"]) != int(command["resulting_ordinal"])
        ):
            entered = command_at
    return entered


def _drift_state(seconds: float) -> str:
    if seconds > DRIFT_THRESHOLD_SECONDS:
        return "behind"
    if seconds < -DRIFT_THRESHOLD_SECONDS:
        return "ahead"
    return "on_track"


def _guidance(*, drift_seconds: float, remaining_seconds: float, overrun_seconds: float, all_ready: bool, status: str) -> list[str]:
    guidance: list[str] = []
    if status == "paused":
        guidance.append("Show Runner timing is paused. Resume only when the creator is ready.")
    if not all_ready:
        guidance.append("Current segment is not fully marked ready: check scene, cue and assets before the next transition.")
    if overrun_seconds >= 120:
        guidance.append("Current segment is more than two minutes over its planned duration; move on at the next natural break if appropriate.")
    elif remaining_seconds <= 60 and remaining_seconds > 0:
        guidance.append("Current segment is inside its final planned minute; prepare the next transition.")
    if drift_seconds > 180:
        guidance.append("The run is more than three minutes behind plan; shorten a later flexible segment rather than rushing a safety-critical cue.")
    elif drift_seconds < -180:
        guidance.append("The run is more than three minutes ahead of plan; use the spare time only if it improves the creator experience.")
    if not guidance:
        guidance.append("Run-of-show timing is stable and the current segment is ready for normal operation.")
    return guidance


def runtime_snapshot(con: sqlite3.Connection, row: sqlite3.Row, *, now: datetime | None = None) -> dict:
    user_id = str(row["user_id"])
    run_id = str(row["id"])
    segments = run_engine._segments(con, user_id, run_id)
    current_ordinal = int(row["current_ordinal"])
    current = next((item for item in segments if int(item["ordinal"]) == current_ordinal), None)
    started_at = _parse_ts(row["started_at"])
    if started_at is None:
        raise HTTPException(500, "LIVE run start time is invalid")
    ended_at = _parse_ts(row["ended_at"])
    horizon = ended_at or now or _utcnow()
    commands = _command_rows(con, user_id, run_id)
    total_active_seconds = _active_elapsed_at(started_at, commands, horizon)
    entered_at = _segment_entered_at(started_at, commands, current_ordinal, horizon)
    entered_active_seconds = _active_elapsed_at(started_at, commands, entered_at)
    current_active_seconds = max(0.0, total_active_seconds - entered_active_seconds)

    total_planned_seconds = sum(int(item["duration_seconds"]) for item in segments)
    planned_before_current = sum(
        int(item["duration_seconds"]) for item in segments if int(item["ordinal"]) < current_ordinal
    )
    current_duration = int(current["duration_seconds"]) if current else 0
    current_budget_progress = min(current_active_seconds, float(current_duration)) if current_duration else 0.0
    schedule_reference = float(planned_before_current) + current_budget_progress
    drift_seconds = total_active_seconds - schedule_reference
    remaining_seconds = max(0.0, float(current_duration) - current_active_seconds)
    overrun_seconds = max(0.0, current_active_seconds - float(current_duration))
    projected_total_active_seconds = max(0.0, float(total_planned_seconds) + drift_seconds)
    projected_remaining_seconds = max(0.0, projected_total_active_seconds - total_active_seconds)

    readiness = [_readiness_row(con, user_id, run_id, int(item["ordinal"])) for item in segments]
    readiness_by_ordinal = {int(item["ordinal"]): item for item in readiness}
    for segment in segments:
        segment["readiness"] = readiness_by_ordinal[int(segment["ordinal"])]
    current_readiness = readiness_by_ordinal.get(
        current_ordinal,
        {
            "ordinal": current_ordinal,
            "cue_ready": False,
            "scene_ready": False,
            "assets_ready": False,
            "all_ready": False,
            "revision": 0,
            "updated_by": "",
            "updated_at": None,
        },
    )
    ready_count = sum(1 for item in readiness if item["all_ready"])
    emergency_mode = show_control.emergency_mode_from_connection(con, user_id)
    automation_suppressed = emergency_mode in {"automation_pause", "safe_hold"}

    return {
        "run_id": run_id,
        "title": row["title"],
        "status": row["status"],
        "run_revision": int(row["revision"]),
        "current_ordinal": current_ordinal,
        "current_segment": current,
        "segment_entered_at": entered_at.isoformat(),
        "timing": {
            "measured_at": horizon.isoformat(),
            "total_active_seconds": round(total_active_seconds, 3),
            "total_planned_seconds": total_planned_seconds,
            "current_segment_active_seconds": round(current_active_seconds, 3),
            "current_segment_planned_seconds": current_duration,
            "current_segment_remaining_seconds": round(remaining_seconds, 3),
            "current_segment_overrun_seconds": round(overrun_seconds, 3),
            "schedule_drift_seconds": round(drift_seconds, 3),
            "schedule_state": _drift_state(drift_seconds),
            "projected_total_active_seconds": round(projected_total_active_seconds, 3),
            "projected_remaining_seconds": round(projected_remaining_seconds, 3),
            "paused": str(row["status"]) == "paused",
        },
        "readiness": {
            "current": current_readiness,
            "segments_ready": ready_count,
            "segments_total": len(readiness),
            "all_segments_ready": bool(readiness) and ready_count == len(readiness),
        },
        "segments": segments,
        "guidance": _guidance(
            drift_seconds=drift_seconds,
            remaining_seconds=remaining_seconds,
            overrun_seconds=overrun_seconds,
            all_ready=bool(current_readiness["all_ready"]),
            status=str(row["status"]),
        ),
        "emergency_mode": emergency_mode,
        "automation_suppressed": automation_suppressed,
        "overlay_automation_allowed": not automation_suppressed,
        "guardian_safeguarding_escalation_preserved": True,
        "provider_write_authority": False,
        "provider_live_controlled": False,
    }


class ReadinessCommand(BaseModel):
    command_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    ordinal: int = Field(ge=1, le=100)
    expected_revision: int = Field(ge=0)
    cue_ready: bool | None = None
    scene_ready: bool | None = None
    assets_ready: bool | None = None


@router.get("/api/live-show/runtime/active")
def get_active_runtime(request: Request):
    member = _member(request)
    with _connect() as con:
        row = run_engine._active_row(con, member.user_id)
        payload = runtime_snapshot(con, row) if row else None
    return JSONResponse(
        {"runtime": payload},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/api/live-show/runs/{run_id}/runtime")
def get_runtime(run_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        row = run_engine._run_row(con, member.user_id, run_id)
        payload = runtime_snapshot(con, row)
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-show/runs/{run_id}/readiness")
def update_readiness(run_id: str, body: ReadinessCommand, request: Request):
    member = _member(request)
    user_id = member.user_id
    actor = f"member:{user_id}"
    requested = {
        "cue_ready": body.cue_ready,
        "scene_ready": body.scene_ready,
        "assets_ready": body.assets_ready,
    }
    if all(value is None for value in requested.values()):
        raise HTTPException(422, "At least one readiness flag must be supplied")

    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        run = run_engine._run_row(con, user_id, run_id)
        if str(run["status"]) not in run_engine.ACTIVE_RUN_STATUSES:
            raise HTTPException(409, "Readiness can only be changed while the LIVE show run is active")
        segment = con.execute(
            "SELECT 1 FROM live_show_run_segments WHERE run_id=? AND user_id=? AND ordinal=?",
            (run_id, user_id, int(body.ordinal)),
        ).fetchone()
        if not segment:
            raise HTTPException(404, "LIVE show run segment not found")

        prior_command = con.execute(
            """SELECT ordinal,requested_cue_ready,requested_scene_ready,requested_assets_ready
               FROM live_show_run_readiness_commands
               WHERE user_id=? AND run_id=? AND command_id=?""",
            (user_id, run_id, body.command_id),
        ).fetchone()
        encoded_requested = {
            "cue_ready": None if body.cue_ready is None else int(bool(body.cue_ready)),
            "scene_ready": None if body.scene_ready is None else int(bool(body.scene_ready)),
            "assets_ready": None if body.assets_ready is None else int(bool(body.assets_ready)),
        }
        if prior_command:
            prior_values = {
                "cue_ready": prior_command["requested_cue_ready"],
                "scene_ready": prior_command["requested_scene_ready"],
                "assets_ready": prior_command["requested_assets_ready"],
            }
            if int(prior_command["ordinal"]) != int(body.ordinal) or prior_values != encoded_requested:
                raise HTTPException(409, "Readiness command ID was already used for a different update")
            payload = runtime_snapshot(con, run)
            payload["duplicate_command"] = True
            return JSONResponse(
                payload,
                headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
            )

        current = _readiness_row(con, user_id, run_id, int(body.ordinal))
        if int(current["revision"]) != int(body.expected_revision):
            raise HTTPException(409, "Segment readiness changed; reload before saving")
        next_values = {
            "cue_ready": current["cue_ready"] if body.cue_ready is None else bool(body.cue_ready),
            "scene_ready": current["scene_ready"] if body.scene_ready is None else bool(body.scene_ready),
            "assets_ready": current["assets_ready"] if body.assets_ready is None else bool(body.assets_ready),
        }
        next_revision = int(current["revision"]) + 1
        now = _now()
        con.execute(
            """INSERT INTO live_show_run_readiness(
                   run_id,user_id,ordinal,cue_ready,scene_ready,assets_ready,revision,updated_by,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id,ordinal) DO UPDATE SET
                   cue_ready=excluded.cue_ready,
                   scene_ready=excluded.scene_ready,
                   assets_ready=excluded.assets_ready,
                   revision=excluded.revision,
                   updated_by=excluded.updated_by,
                   updated_at=excluded.updated_at""",
            (
                run_id,
                user_id,
                int(body.ordinal),
                int(next_values["cue_ready"]),
                int(next_values["scene_ready"]),
                int(next_values["assets_ready"]),
                next_revision,
                actor,
                now,
            ),
        )
        con.execute(
            """INSERT INTO live_show_run_readiness_commands(
                   user_id,run_id,command_id,ordinal,requested_cue_ready,requested_scene_ready,
                   requested_assets_ready,resulting_revision,actor,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                run_id,
                body.command_id,
                int(body.ordinal),
                encoded_requested["cue_ready"],
                encoded_requested["scene_ready"],
                encoded_requested["assets_ready"],
                next_revision,
                actor,
                now,
            ),
        )
        con.execute(
            """DELETE FROM live_show_run_readiness_commands
               WHERE user_id=? AND rowid NOT IN (
                   SELECT rowid FROM live_show_run_readiness_commands
                   WHERE user_id=? ORDER BY created_at DESC LIMIT 1000
               )""",
            (user_id, user_id),
        )
        payload = runtime_snapshot(con, run)
        payload["duplicate_command"] = False
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


def overlay_safe_payload(runtime: dict | None) -> dict | None:
    if not runtime:
        return None
    current = runtime.get("current_segment") or {}
    timing = runtime.get("timing") or {}
    readiness = (runtime.get("readiness") or {}).get("current") or {}
    return {
        "run_id": runtime.get("run_id"),
        "status": runtime.get("status"),
        "current_ordinal": runtime.get("current_ordinal"),
        "segment_title": current.get("title"),
        "segment_type": current.get("segment_type"),
        "scene_name": current.get("scene_name"),
        "timing": {
            "remaining_seconds": timing.get("current_segment_remaining_seconds"),
            "overrun_seconds": timing.get("current_segment_overrun_seconds"),
            "schedule_drift_seconds": timing.get("schedule_drift_seconds"),
            "schedule_state": timing.get("schedule_state"),
            "paused": timing.get("paused"),
        },
        "readiness": {
            "cue_ready": bool(readiness.get("cue_ready")),
            "scene_ready": bool(readiness.get("scene_ready")),
            "assets_ready": bool(readiness.get("assets_ready")),
            "all_ready": bool(readiness.get("all_ready")),
        },
        "emergency_mode": runtime.get("emergency_mode"),
        "automation_suppressed": bool(runtime.get("automation_suppressed")),
        "overlay_automation_allowed": bool(runtime.get("overlay_automation_allowed")),
        "guardian_safeguarding_escalation_preserved": True,
        "provider_write_authority": False,
        "privacy_boundary": "No cue label, Auto Cue script text, member identity or private readiness audit metadata is exposed by this overlay-safe view.",
    }


@router.get("/api/live-show/runtime/overlay-safe")
def get_overlay_safe_runtime(request: Request):
    member = _member(request)
    with _connect() as con:
        row = run_engine._active_row(con, member.user_id)
        runtime = runtime_snapshot(con, row) if row else None
        payload = overlay_safe_payload(runtime)
    return JSONResponse(
        {"overlay": payload},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay-studio/live-director", response_class=HTMLResponse, include_in_schema=False)
def live_director_page(request: Request):
    member = _member(request)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    product = escape(PRODUCT_FULL_NAME)
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow"><title>LIVE Director — __PRODUCT__</title><style>
:root{--bg:#070812;--card:#111526;--line:#ffffff20;--muted:#bdc6d8;--gold:#efc96b;--violet:#9b72ff;--good:#80e6b1;--warn:#ffd37a;--danger:#ff8ca6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1120px,calc(100% - 28px));margin:auto;padding:30px 0 70px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted);line-height:1.55}.metric{font-size:2rem;font-weight:900}.badge{display:inline-block;border:1px solid #ffffff25;border-radius:999px;padding:5px 8px;font-size:.8rem}.btn,button{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px;cursor:pointer;font-weight:850;text-decoration:none;display:inline-block}.primary{background:linear-gradient(120deg,var(--gold),var(--violet));color:#160b22;border:0}.checks label{display:flex;gap:8px;align-items:center;margin:9px 0}.checks input{width:20px;height:20px}.good{color:var(--good)}.warn{color:var(--warn)}.danger{color:var(--danger)}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style></head><body><main class="wrap"><p><a class="btn" href="/live-overlay-studio/show-runner">← Show Runner</a> <a class="btn" href="/live-overlay-studio/show-builder">Show Builder</a> <a class="btn" href="/live-overlay-studio/prompter">Private Auto Cue</a> <a class="btn" href="/live-guardian">LIVE Guardian</a></p><div style="color:var(--gold);font-weight:900">Powered by Aura AI · Creator LIVE Production</div><h1>LIVE Director</h1><p class="muted">Welcome __DISPLAY_NAME__. This private director view derives timing from the audited Show Runner lifecycle, tracks readiness without storing spoken script text, and exposes only a separate privacy-filtered status to overlay automation.</p><div id="app" class="card muted">Loading active run…</div><div class="card"><b>Safety boundary:</b><p class="muted">LIVE Director does not start, end, mute, kick, battle, invite guests or moderate on the provider. Emergency Safe Hold can suppress overlay automation while manual creator controls and LIVE Guardian safeguarding remain available.</p></div></main><script>
const $=id=>document.getElementById(id);const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');let runtime=null;async function api(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Request failed');return d}const fmt=s=>{s=Math.max(0,Math.round(Number(s||0)));const m=Math.floor(s/60),x=s%60;return `${m}:${String(x).padStart(2,'0')}`};function stateClass(s){return s==='behind'?'danger':s==='ahead'?'warn':'good'}async function load(){const d=await api('/api/live-show/runtime/active');runtime=d.runtime;render()}function render(){if(!runtime){$('app').className='card muted';$('app').innerHTML='No active Show Runner session. Start a ready show first.';return}const c=runtime.current_segment||{},t=runtime.timing||{},r=(runtime.readiness||{}).current||{},guidance=runtime.guidance||[];$('app').className='';$('app').innerHTML=`<div class="grid"><section class="card"><div class="row"><span class="badge">${esc(runtime.status)}</span><span class="badge">Segment ${runtime.current_ordinal}</span><span class="badge">Emergency ${esc(runtime.emergency_mode)}</span></div><h2>${esc(c.title||'Current segment')}</h2><p class="muted">Scene: ${esc(c.scene_name||'—')} · Private cue label: ${esc(c.cue_label||'—')}</p><div class="grid"><div><div class="muted">Segment remaining</div><div class="metric">${fmt(t.current_segment_remaining_seconds)}</div></div><div><div class="muted">Schedule drift</div><div class="metric ${stateClass(t.schedule_state)}">${t.schedule_drift_seconds>0?'+':''}${Math.round(t.schedule_drift_seconds||0)}s</div></div></div><p class="muted">Active ${fmt(t.current_segment_active_seconds)} / planned ${fmt(t.current_segment_planned_seconds)} · projected show remaining ${fmt(t.projected_remaining_seconds)}.</p></section><section class="card checks"><h2>Segment readiness</h2><label><input id="sceneReady" type="checkbox" ${r.scene_ready?'checked':''}> Scene ready</label><label><input id="cueReady" type="checkbox" ${r.cue_ready?'checked':''}> Cue ready</label><label><input id="assetsReady" type="checkbox" ${r.assets_ready?'checked':''}> Assets ready</label><button id="saveReady" class="primary">Save readiness</button><p class="muted">Readiness revision ${r.revision||0}. ${r.all_ready?'<span class="good"><b>All ready.</b></span>':'Not all ready yet.'}</p><p class="muted">Overlay automation allowed: <b>${runtime.overlay_automation_allowed?'Yes':'No'}</b>. Guardian safeguarding preserved: <b>Yes</b>.</p></section></div><section class="card"><h2>Aura director guidance</h2>${guidance.map(x=>`<p>• ${esc(x)}</p>`).join('')}</section>`;$('saveReady').onclick=saveReadiness}async function saveReadiness(){const r=(runtime.readiness||{}).current||{};try{runtime=await api('/api/live-show/runs/'+encodeURIComponent(runtime.run_id)+'/readiness',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command_id:'ready_'+crypto.randomUUID().replaceAll('-',''),ordinal:runtime.current_ordinal,expected_revision:r.revision||0,scene_ready:$('sceneReady').checked,cue_ready:$('cueReady').checked,assets_ready:$('assetsReady').checked})});render()}catch(e){alert(e.message);await load()}}load().catch(e=>$('app').textContent=e.message);setInterval(()=>load().catch(()=>{}),5000);
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
    "ReadinessCommand",
    "runtime_snapshot",
    "overlay_safe_payload",
    "get_active_runtime",
    "get_runtime",
    "update_readiness",
    "get_overlay_safe_runtime",
]
