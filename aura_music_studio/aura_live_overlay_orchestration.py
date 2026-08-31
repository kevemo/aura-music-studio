from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import aura_live_overlay_studio as overlay_studio
from . import aura_live_run_engine as run_engine
from . import aura_live_runtime_intelligence as runtime_intelligence
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Overlay Orchestration"])
TRIGGER_LIMIT = 1000
PUBLIC_TRIGGER_KINDS = {"segment_entered", "final_minute"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _utcnow().isoformat()


def _connect() -> sqlite3.Connection:
    return runtime_intelligence._connect()


def _init_schema() -> None:
    runtime_intelligence._init_schema()
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_show_overlay_orchestration_profiles (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                show_segment_card INTEGER NOT NULL DEFAULT 1,
                show_timer INTEGER NOT NULL DEFAULT 1,
                show_scene_name INTEGER NOT NULL DEFAULT 0,
                show_schedule_status INTEGER NOT NULL DEFAULT 0,
                transition_flash INTEGER NOT NULL DEFAULT 1,
                final_minute_flash INTEGER NOT NULL DEFAULT 1,
                countdown_warning_seconds INTEGER NOT NULL DEFAULT 60,
                overrun_warning_seconds INTEGER NOT NULL DEFAULT 120,
                drift_warning_seconds INTEGER NOT NULL DEFAULT 180,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                CHECK(enabled IN (0,1)),
                CHECK(show_segment_card IN (0,1)),
                CHECK(show_timer IN (0,1)),
                CHECK(show_scene_name IN (0,1)),
                CHECK(show_schedule_status IN (0,1)),
                CHECK(transition_flash IN (0,1)),
                CHECK(final_minute_flash IN (0,1)),
                CHECK(countdown_warning_seconds BETWEEN 0 AND 300),
                CHECK(overrun_warning_seconds BETWEEN 0 AND 1800),
                CHECK(drift_warning_seconds BETWEEN 120 AND 1800),
                CHECK(revision >= 1)
            );

            CREATE TABLE IF NOT EXISTS live_show_overlay_orchestration_commands (
                user_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                request_json TEXT NOT NULL,
                resulting_revision INTEGER NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,command_id)
            );

            CREATE TABLE IF NOT EXISTS live_show_overlay_orchestration_triggers (
                user_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source_delivered_at TEXT,
                PRIMARY KEY(user_id,run_id,event_key),
                FOREIGN KEY(run_id) REFERENCES live_show_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_live_show_overlay_orchestration_trigger_time
                ON live_show_overlay_orchestration_triggers(user_id,created_at DESC);
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _ensure_profile(con: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM live_show_overlay_orchestration_profiles WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if row:
        return row
    con.execute(
        """INSERT INTO live_show_overlay_orchestration_profiles(
               user_id,updated_by,updated_at
           ) VALUES(?,?,?)""",
        (user_id, f"member:{user_id}", _now()),
    )
    return con.execute(
        "SELECT * FROM live_show_overlay_orchestration_profiles WHERE user_id=?",
        (user_id,),
    ).fetchone()


def _profile_payload(row: sqlite3.Row) -> dict:
    return {
        "enabled": bool(row["enabled"]),
        "show_segment_card": bool(row["show_segment_card"]),
        "show_timer": bool(row["show_timer"]),
        "show_scene_name": bool(row["show_scene_name"]),
        "show_schedule_status": bool(row["show_schedule_status"]),
        "transition_flash": bool(row["transition_flash"]),
        "final_minute_flash": bool(row["final_minute_flash"]),
        "countdown_warning_seconds": int(row["countdown_warning_seconds"]),
        "overrun_warning_seconds": int(row["overrun_warning_seconds"]),
        "drift_warning_seconds": int(row["drift_warning_seconds"]),
        "revision": int(row["revision"]),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


class OrchestrationProfilePatch(BaseModel):
    command_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    expected_revision: int = Field(ge=1)
    enabled: bool | None = None
    show_segment_card: bool | None = None
    show_timer: bool | None = None
    show_scene_name: bool | None = None
    show_schedule_status: bool | None = None
    transition_flash: bool | None = None
    final_minute_flash: bool | None = None
    countdown_warning_seconds: int | None = Field(default=None, ge=0, le=300)
    overrun_warning_seconds: int | None = Field(default=None, ge=0, le=1800)
    drift_warning_seconds: int | None = Field(default=None, ge=120, le=1800)


def _active_runtime(user_id: str, *, now: datetime | None = None) -> dict | None:
    with _connect() as con:
        row = run_engine._active_row(con, user_id)
        if not row:
            return None
        return runtime_intelligence.runtime_snapshot(con, row, now=now or _utcnow())


def _insert_trigger(
    con: sqlite3.Connection,
    *,
    user_id: str,
    run_id: str,
    event_key: str,
    kind: str,
    ordinal: int,
    message: str,
    created_at: str,
) -> bool:
    try:
        con.execute(
            """INSERT INTO live_show_overlay_orchestration_triggers(
                   user_id,run_id,event_key,kind,ordinal,message,created_at,source_delivered_at
               ) VALUES(?,?,?,?,?,?,?,NULL)""",
            (user_id, run_id, event_key, kind, ordinal, message[:180], created_at),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def _evaluate_triggers(
    con: sqlite3.Connection,
    *,
    user_id: str,
    runtime: dict,
    profile: dict,
    created_at: str,
) -> list[str]:
    if not profile["enabled"] or not runtime.get("overlay_automation_allowed"):
        return []
    if runtime.get("status") not in run_engine.ACTIVE_RUN_STATUSES:
        return []

    run_id = str(runtime["run_id"])
    ordinal = int(runtime["current_ordinal"])
    current = runtime.get("current_segment") or {}
    timing = runtime.get("timing") or {}
    created: list[str] = []

    if _insert_trigger(
        con,
        user_id=user_id,
        run_id=run_id,
        event_key=f"segment:{ordinal}",
        kind="segment_entered",
        ordinal=ordinal,
        message=f"Now: {str(current.get('title') or f'Segment {ordinal}')}",
        created_at=created_at,
    ):
        created.append("segment_entered")

    remaining = float(timing.get("current_segment_remaining_seconds") or 0)
    countdown = int(profile["countdown_warning_seconds"])
    if countdown > 0 and 0 < remaining <= countdown:
        if _insert_trigger(
            con,
            user_id=user_id,
            run_id=run_id,
            event_key=f"final-minute:{ordinal}",
            kind="final_minute",
            ordinal=ordinal,
            message=f"{round(remaining)} seconds remain in the current segment",
            created_at=created_at,
        ):
            created.append("final_minute")

    overrun = float(timing.get("current_segment_overrun_seconds") or 0)
    overrun_threshold = int(profile["overrun_warning_seconds"])
    if overrun_threshold > 0 and overrun >= overrun_threshold:
        if _insert_trigger(
            con,
            user_id=user_id,
            run_id=run_id,
            event_key=f"overrun:{ordinal}",
            kind="overrun",
            ordinal=ordinal,
            message=f"Segment {ordinal} is {round(overrun)} seconds over plan",
            created_at=created_at,
        ):
            created.append("overrun")

    drift = float(timing.get("schedule_drift_seconds") or 0)
    drift_threshold = int(profile["drift_warning_seconds"])
    if drift >= drift_threshold:
        if _insert_trigger(
            con,
            user_id=user_id,
            run_id=run_id,
            event_key=f"drift-behind:{ordinal}",
            kind="drift_behind",
            ordinal=ordinal,
            message=f"Run is {round(drift)} seconds behind plan",
            created_at=created_at,
        ):
            created.append("drift_behind")
    elif drift <= -drift_threshold:
        if _insert_trigger(
            con,
            user_id=user_id,
            run_id=run_id,
            event_key=f"drift-ahead:{ordinal}",
            kind="drift_ahead",
            ordinal=ordinal,
            message=f"Run is {round(abs(drift))} seconds ahead of plan",
            created_at=created_at,
        ):
            created.append("drift_ahead")

    con.execute(
        """DELETE FROM live_show_overlay_orchestration_triggers
           WHERE user_id=? AND rowid NOT IN (
               SELECT rowid FROM live_show_overlay_orchestration_triggers
               WHERE user_id=? ORDER BY created_at DESC LIMIT ?
           )""",
        (user_id, user_id, TRIGGER_LIMIT),
    )
    return created


def _recent_triggers(con: sqlite3.Connection, user_id: str, *, limit: int = 20) -> list[dict]:
    rows = con.execute(
        """SELECT run_id,event_key,kind,ordinal,message,created_at,source_delivered_at
           FROM live_show_overlay_orchestration_triggers
           WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?""",
        (user_id, max(1, min(limit, 100))),
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "event_key": row["event_key"],
            "kind": row["kind"],
            "ordinal": int(row["ordinal"]),
            "message": row["message"],
            "created_at": row["created_at"],
            "source_delivered_at": row["source_delivered_at"],
        }
        for row in rows
    ]


def _source_triggers(
    con: sqlite3.Connection,
    *,
    user_id: str,
    run_id: str,
    profile: dict,
    delivered_at: str,
) -> list[dict]:
    rows = con.execute(
        """SELECT event_key,kind,ordinal
           FROM live_show_overlay_orchestration_triggers
           WHERE user_id=? AND run_id=? AND source_delivered_at IS NULL
             AND kind IN ('segment_entered','final_minute')
           ORDER BY created_at,rowid LIMIT 20""",
        (user_id, run_id),
    ).fetchall()
    allowed: list[dict] = []
    delivered_keys: list[str] = []
    for row in rows:
        kind = str(row["kind"])
        should_deliver = (
            (kind == "segment_entered" and profile["transition_flash"])
            or (kind == "final_minute" and profile["final_minute_flash"])
        )
        delivered_keys.append(str(row["event_key"]))
        if should_deliver:
            allowed.append(
                {
                    "kind": kind,
                    "ordinal": int(row["ordinal"]),
                }
            )
    for event_key in delivered_keys:
        con.execute(
            """UPDATE live_show_overlay_orchestration_triggers
               SET source_delivered_at=?
               WHERE user_id=? AND run_id=? AND event_key=? AND source_delivered_at IS NULL""",
            (delivered_at, user_id, run_id, event_key),
        )
    return allowed


def _presentation(runtime: dict, profile: dict) -> dict:
    safe = runtime_intelligence.overlay_safe_payload(runtime) or {}
    timing = safe.get("timing") or {}
    enabled = bool(profile["enabled"]) and bool(runtime.get("overlay_automation_allowed"))
    return {
        "visible": enabled and bool(profile["show_segment_card"]),
        "segment_title": safe.get("segment_title") if enabled else None,
        "scene_name": (
            safe.get("scene_name")
            if enabled and profile["show_scene_name"]
            else None
        ),
        "remaining_seconds": (
            timing.get("remaining_seconds")
            if enabled and profile["show_timer"]
            else None
        ),
        "schedule_state": (
            timing.get("schedule_state")
            if enabled and profile["show_schedule_status"]
            else None
        ),
        "paused": bool(timing.get("paused")) if enabled else False,
        "show_timer": enabled and bool(profile["show_timer"]),
        "show_scene_name": enabled and bool(profile["show_scene_name"]),
        "show_schedule_status": enabled and bool(profile["show_schedule_status"]),
    }


def orchestration_pulse(
    user_id: str,
    *,
    consume_source: bool = False,
    now: datetime | None = None,
) -> dict:
    measured = now or _utcnow()
    created_at = measured.isoformat()
    runtime = _active_runtime(user_id, now=measured)
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        profile_row = _ensure_profile(con, user_id)
        profile = _profile_payload(profile_row)

        if not runtime:
            return {
                "active": False,
                "suppressed": False,
                "profile": profile,
                "presentation": {"visible": False},
                "new_trigger_kinds": [],
                "source_triggers": [],
                "recent_triggers": _recent_triggers(con, user_id),
                "provider_write_authority": False,
                "provider_live_controlled": False,
                "guardian_safeguarding_escalation_preserved": True,
            }

        suppressed = bool(runtime.get("automation_suppressed")) or not bool(profile["enabled"])
        new_trigger_kinds = _evaluate_triggers(
            con,
            user_id=user_id,
            runtime=runtime,
            profile=profile,
            created_at=created_at,
        )
        source_triggers: list[dict] = []
        if consume_source and not suppressed:
            source_triggers = _source_triggers(
                con,
                user_id=user_id,
                run_id=str(runtime["run_id"]),
                profile=profile,
                delivered_at=created_at,
            )

        return {
            "active": True,
            "run_id": runtime["run_id"],
            "status": runtime["status"],
            "current_ordinal": runtime["current_ordinal"],
            "suppressed": suppressed,
            "emergency_mode": runtime["emergency_mode"],
            "profile": profile,
            "presentation": _presentation(runtime, profile) if not suppressed else {"visible": False},
            "new_trigger_kinds": new_trigger_kinds,
            "source_triggers": source_triggers,
            "recent_triggers": _recent_triggers(con, user_id),
            "timing": {
                "remaining_seconds": runtime["timing"]["current_segment_remaining_seconds"],
                "overrun_seconds": runtime["timing"]["current_segment_overrun_seconds"],
                "schedule_drift_seconds": runtime["timing"]["schedule_drift_seconds"],
                "schedule_state": runtime["timing"]["schedule_state"],
                "paused": runtime["timing"]["paused"],
            },
            "provider_write_authority": False,
            "provider_live_controlled": False,
            "manual_provider_actions_required": True,
            "actions_scope": "Aura browser-source visuals only",
            "guardian_safeguarding_escalation_preserved": True,
        }


def source_safe_pulse(user_id: str, *, now: datetime | None = None) -> dict:
    pulse = orchestration_pulse(user_id, consume_source=True, now=now)
    return {
        "active": bool(pulse["active"]),
        "suppressed": bool(pulse["suppressed"]),
        "emergency_mode": pulse.get("emergency_mode", "normal"),
        "presentation": pulse.get("presentation") or {"visible": False},
        "triggers": pulse.get("source_triggers") or [],
        "provider_write_authority": False,
        "provider_live_controlled": False,
        "guardian_safeguarding_escalation_preserved": True,
        "privacy_boundary": (
            "No Auto Cue script text, cue label, member identity, readiness audit metadata, "
            "or provider control credential is exposed by this orchestration source."
        ),
    }


@router.get("/api/live-show/orchestration")
def get_orchestration(request: Request):
    member = _member(request)
    return JSONResponse(
        orchestration_pulse(member.user_id, consume_source=False),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/api/live-show/orchestration")
def update_orchestration(body: OrchestrationProfilePatch, request: Request):
    member = _member(request)
    user_id = member.user_id
    actor = f"member:{user_id}"
    values = body.model_dump(
        exclude={"command_id", "expected_revision"},
        exclude_none=True,
    )
    if not values:
        raise HTTPException(422, "At least one orchestration setting must be supplied")

    request_payload = body.model_dump(exclude={"command_id"}, exclude_none=True)
    request_json = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
    duplicate = False
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        current = _ensure_profile(con, user_id)
        prior = con.execute(
            """SELECT request_json FROM live_show_overlay_orchestration_commands
               WHERE user_id=? AND command_id=?""",
            (user_id, body.command_id),
        ).fetchone()
        if prior:
            if str(prior["request_json"]) != request_json:
                raise HTTPException(409, "Orchestration command ID was already used for different settings")
            duplicate = True
        else:
            if int(current["revision"]) != int(body.expected_revision):
                raise HTTPException(409, "Overlay orchestration settings changed; reload before saving")
            normalized = {
                key: int(bool(value)) if isinstance(value, bool) else int(value)
                for key, value in values.items()
            }
            next_revision = int(current["revision"]) + 1
            now_value = _now()
            assignments = ",".join(f"{key}=?" for key in normalized)
            changed = con.execute(
                f"""UPDATE live_show_overlay_orchestration_profiles
                    SET {assignments},revision=?,updated_by=?,updated_at=?
                    WHERE user_id=? AND revision=?""",
                [
                    *normalized.values(),
                    next_revision,
                    actor,
                    now_value,
                    user_id,
                    int(body.expected_revision),
                ],
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "Overlay orchestration settings changed; reload before saving")
            con.execute(
                """INSERT INTO live_show_overlay_orchestration_commands(
                       user_id,command_id,request_json,resulting_revision,actor,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (user_id, body.command_id, request_json, next_revision, actor, now_value),
            )
            con.execute(
                """DELETE FROM live_show_overlay_orchestration_commands
                   WHERE user_id=? AND rowid NOT IN (
                       SELECT rowid FROM live_show_overlay_orchestration_commands
                       WHERE user_id=? ORDER BY created_at DESC LIMIT 1000
                   )""",
                (user_id, user_id),
            )

    payload = orchestration_pulse(user_id, consume_source=False)
    payload["duplicate_command"] = duplicate
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/api/live-show/orchestration/pulse")
def member_orchestration_pulse(request: Request):
    member = _member(request)
    return JSONResponse(
        orchestration_pulse(member.user_id, consume_source=False),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay/source/{token}/orchestration/pulse", include_in_schema=False)
def source_orchestration_pulse(token: str):
    user_id = overlay_studio._user_for_source(token)
    return JSONResponse(
        source_safe_pulse(user_id),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.get("/live-overlay/source/{token}/orchestrated", response_class=HTMLResponse, include_in_schema=False)
def orchestrated_overlay_source(token: str):
    overlay_studio._user_for_source(token)
    token_json = json.dumps(token)
    base_source = f"/live-overlay/source/{quote(token, safe='')}"
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent;font-family:Inter,Arial,sans-serif;color:#fff}iframe{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent;z-index:1}.runtime{position:absolute;z-index:5;left:4%;top:5%;min-width:220px;max-width:52%;padding:12px 16px;border-radius:16px;background:linear-gradient(135deg,#11172eea,#17283be6);border:1px solid #ffffff38;box-shadow:0 12px 42px #0008;opacity:0;transform:translateY(-10px);transition:.28s;pointer-events:none}.runtime.show{opacity:1;transform:none}.title{font-weight:900;font-size:clamp(16px,2vw,26px);color:#efc96b}.meta{margin-top:4px;font-size:clamp(12px,1.2vw,17px);opacity:.9}.flash{position:absolute;z-index:6;left:50%;top:16%;transform:translate(-50%,-16px) scale(.98);opacity:0;min-width:260px;max-width:78%;padding:16px 22px;border-radius:20px;background:linear-gradient(135deg,#180f2dee,#0c2634ee);border:1px solid #ffffff42;box-shadow:0 16px 55px #0009;text-align:center;font-weight:900;font-size:clamp(16px,2vw,28px);transition:.3s;pointer-events:none}.flash.show{opacity:1;transform:translate(-50%,0) scale(1)}.runtime.pulse{animation:pulse .75s ease}@keyframes pulse{50%{box-shadow:0 0 0 8px #efc96b33,0 12px 42px #0008}}
</style></head><body><iframe title="Aura LIVE Overlay" src="__BASE_SOURCE__"></iframe><div id="runtime" class="runtime"><div id="title" class="title"></div><div id="meta" class="meta"></div></div><div id="flash" class="flash"></div><script>
'use strict';const TOKEN=__TOKEN__;const $=id=>document.getElementById(id);let flashTimer=null;const clean=v=>String(v??'').slice(0,160);function fmt(s){s=Math.max(0,Math.round(Number(s||0)));const m=Math.floor(s/60),r=s%60;return `${m}:${String(r).padStart(2,'0')}`}function flash(text){const e=$('flash');e.textContent=clean(text);e.classList.add('show');clearTimeout(flashTimer);flashTimer=setTimeout(()=>e.classList.remove('show'),3000)}function render(d){const p=d.presentation||{},box=$('runtime');if(!d.active||d.suppressed||!p.visible){box.classList.remove('show');return}box.classList.add('show');$('title').textContent=clean(p.segment_title||'LIVE');const meta=[];if(p.show_timer&&p.remaining_seconds!==null&&p.remaining_seconds!==undefined)meta.push(fmt(p.remaining_seconds));if(p.show_scene_name&&p.scene_name)meta.push(clean(p.scene_name));if(p.show_schedule_status&&p.schedule_state)meta.push(clean(p.schedule_state).replaceAll('_',' '));if(p.paused)meta.push('paused');$('meta').textContent=meta.join(' · ');for(const t of d.triggers||[]){if(t.kind==='segment_entered')flash('Now: '+clean(p.segment_title||'Next segment'));if(t.kind==='final_minute'){box.classList.remove('pulse');void box.offsetWidth;box.classList.add('pulse')}}}async function pulse(){try{const r=await fetch('/live-overlay/source/'+encodeURIComponent(TOKEN)+'/orchestration/pulse',{cache:'no-store'});if(r.ok)render(await r.json())}catch{}finally{setTimeout(pulse,1000)}}pulse();
</script></body></html>'''.replace("__TOKEN__", token_json).replace("__BASE_SOURCE__", escape(base_source, quote=True))
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.get("/live-overlay-studio/orchestration", response_class=HTMLResponse, include_in_schema=False)
def orchestration_page(request: Request):
    _member(request)
    product = escape(PRODUCT_FULL_NAME)
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow"><title>LIVE Overlay Orchestration — __PRODUCT__</title><style>
:root{--bg:#070812;--card:#111526;--line:#ffffff20;--muted:#bdc6d8;--gold:#efc96b;--violet:#9b72ff;--good:#80e6b1;--danger:#ff8ca6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1120px,calc(100% - 28px));margin:auto;padding:30px 0 70px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted);line-height:1.55}.btn,button,input{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px}.btn,button{cursor:pointer;font-weight:850;text-decoration:none;display:inline-block}.primary{background:linear-gradient(120deg,var(--gold),var(--violet));color:#160b22;border:0}.toggle{display:flex;justify-content:space-between;gap:12px;align-items:center;border-bottom:1px solid #ffffff12;padding:9px 0}.toggle input{width:20px;height:20px}.good{color:var(--good)}code{display:block;word-break:break-all;background:#070a13;border-radius:10px;padding:12px;color:var(--gold)}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style></head><body><main class="wrap"><p><a class="btn" href="/live-overlay-studio">← Overlay Studio</a> <a class="btn" href="/live-overlay-studio/live-director">LIVE Director</a> <a class="btn" href="/live-overlay-studio/show-runner">Show Runner</a></p><div style="color:var(--gold);font-weight:900">Powered by Aura AI · Creator LIVE Production</div><h1>LIVE Overlay Orchestration</h1><p class="muted">Add a runtime-aware visual layer above the existing Aura browser source without changing its gift, welcome, audio or event behavior. Automation is limited to Aura-owned browser-source presentation.</p><div class="grid"><section class="card"><h2>Runtime presentation</h2><div class="toggle"><span>Enable orchestration</span><input id="enabled" type="checkbox"></div><div class="toggle"><span>Show current segment card</span><input id="segment" type="checkbox"></div><div class="toggle"><span>Show segment timer</span><input id="timer" type="checkbox"></div><div class="toggle"><span>Show scene name</span><input id="scene" type="checkbox"></div><div class="toggle"><span>Show schedule status</span><input id="schedule" type="checkbox"></div><div class="toggle"><span>Flash on segment transition</span><input id="transition" type="checkbox"></div><div class="toggle"><span>Pulse in final minute</span><input id="final" type="checkbox"></div><p><label>Final-minute threshold (seconds)<br><input id="countdown" type="number" min="0" max="300"></label></p><p><label>Private overrun warning (seconds)<br><input id="overrun" type="number" min="0" max="1800"></label></p><p><label>Private schedule-drift warning (seconds)<br><input id="drift" type="number" min="120" max="1800"></label></p><button id="save" class="primary">Save orchestration</button><p id="saveStatus" class="muted"></p></section><section class="card"><h2>One-source setup</h2><p class="muted">Rotate the existing private Aura source credential, then use the orchestrated URL below as the single Link/Browser Source in TikTok LIVE Studio, OBS or compatible broadcast software.</p><button id="rotate" class="primary">Generate orchestrated source URL</button><code id="source">A private URL is revealed only after rotation.</code><p class="muted">Rotation immediately invalidates the previous private source URL.</p><h3>Provider boundary</h3><p class="muted">This layer cannot start/end LIVE, invite guests, mute/kick viewers, moderate chat or control battles. LIVE Guardian remains independent. Safe Hold/Automation Pause suppresses this runtime automation.</p></section></div><section class="card"><h2>Current orchestration state</h2><div id="state" class="muted">Loading…</div></section><section class="card"><h2>Recent private runtime triggers</h2><div id="triggers" class="muted">No runtime triggers yet.</div></section></main><script>
const $=id=>document.getElementById(id);let data=null;const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');async function api(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Request failed');return d}function render(){if(!data)return;const p=data.profile||{};$('enabled').checked=!!p.enabled;$('segment').checked=!!p.show_segment_card;$('timer').checked=!!p.show_timer;$('scene').checked=!!p.show_scene_name;$('schedule').checked=!!p.show_schedule_status;$('transition').checked=!!p.transition_flash;$('final').checked=!!p.final_minute_flash;$('countdown').value=p.countdown_warning_seconds??60;$('overrun').value=p.overrun_warning_seconds??120;$('drift').value=p.drift_warning_seconds??180;$('state').innerHTML=data.active?`<b class="good">Active run</b> · segment ${data.current_ordinal} · emergency ${esc(data.emergency_mode)} · overlay automation ${data.suppressed?'suppressed':'allowed'} · provider write authority: none`:'No active Show Runner session. Settings are ready for the next run.';const rows=data.recent_triggers||[];$('triggers').innerHTML=rows.length?rows.map(x=>`<p><b>${esc(x.kind.replaceAll('_',' '))}</b> · segment ${x.ordinal}<br><span class="muted">${esc(x.message)} · ${esc(x.created_at)}</span></p>`).join(''):'No runtime triggers yet.'}async function load(){data=await api('/api/live-show/orchestration');render()}$('save').onclick=async()=>{try{data=await api('/api/live-show/orchestration',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({command_id:'orch_'+crypto.randomUUID().replaceAll('-',''),expected_revision:data.profile.revision,enabled:$('enabled').checked,show_segment_card:$('segment').checked,show_timer:$('timer').checked,show_scene_name:$('scene').checked,show_schedule_status:$('schedule').checked,transition_flash:$('transition').checked,final_minute_flash:$('final').checked,countdown_warning_seconds:Number($('countdown').value),overrun_warning_seconds:Number($('overrun').value),drift_warning_seconds:Number($('drift').value)})});$('saveStatus').textContent='Saved.';render()}catch(e){$('saveStatus').textContent=e.message;await load()}};$('rotate').onclick=async()=>{try{const d=await api('/api/live-overlay/rotate-source',{method:'POST'});$('source').textContent=d.source_url.replace(/\/$/,'')+'/orchestrated'}catch(e){$('source').textContent=e.message}};load().catch(e=>$('state').textContent=e.message);setInterval(()=>api('/api/live-show/orchestration/pulse').then(d=>{data=d;render()}).catch(()=>{}),5000);
</script></body></html>'''.replace("__PRODUCT__", product)
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
    "OrchestrationProfilePatch",
    "orchestration_pulse",
    "source_safe_pulse",
    "get_orchestration",
    "update_orchestration",
    "member_orchestration_pulse",
    "source_orchestration_pulse",
    "orchestrated_overlay_source",
]
