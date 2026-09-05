from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import aura_live_run_engine as run_engine
from . import aura_live_runtime_intelligence as runtime_intelligence
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Post-Show Reports"])
TERMINAL_STATUSES = {"completed", "aborted"}


def _connect():
    return runtime_intelligence._connect()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds())


def _run_commands(con, user_id: str, run_id: str):
    return con.execute(
        """SELECT action,requested_ordinal,previous_status,previous_ordinal,
                  resulting_status,resulting_ordinal,created_at
           FROM live_show_run_commands
           WHERE user_id=? AND run_id=?
           ORDER BY created_at,rowid""",
        (user_id, run_id),
    ).fetchall()


def _readiness_commands(con, user_id: str, run_id: str):
    return con.execute(
        """SELECT ordinal,requested_cue_ready,requested_scene_ready,
                  requested_assets_ready,created_at
           FROM live_show_run_readiness_commands
           WHERE user_id=? AND run_id=?
           ORDER BY created_at,rowid""",
        (user_id, run_id),
    ).fetchall()


def _timeline(row, commands, segment_ordinals: set[int]) -> dict:
    started_at = _parse(row["started_at"])
    ended_at = _parse(row["ended_at"])
    if started_at is None or ended_at is None:
        raise HTTPException(409, "Post-show reports are available only after the Show Runner has ended")

    active_by_ordinal: dict[int, float] = defaultdict(float)
    visits: dict[int, int] = defaultdict(int)
    first_entry: dict[int, datetime] = {1: started_at}
    visits[1] = 1
    action_counts: dict[str, int] = defaultdict(int)
    paused_seconds = 0.0
    state = "running"
    ordinal = 1
    cursor = started_at

    for command in commands:
        at = _parse(command["created_at"])
        if at is None or at < started_at:
            continue
        if at > ended_at:
            break
        interval = _seconds(cursor, at) if at >= cursor else 0.0
        if state == "running" and ordinal in segment_ordinals:
            active_by_ordinal[ordinal] += interval
        elif state == "paused":
            paused_seconds += interval

        action = str(command["action"])
        action_counts[action] += 1
        previous_ordinal = int(command["previous_ordinal"])
        resulting_ordinal = int(command["resulting_ordinal"])
        if resulting_ordinal != previous_ordinal and resulting_ordinal in segment_ordinals:
            visits[resulting_ordinal] += 1
            first_entry.setdefault(resulting_ordinal, at)

        state = str(command["resulting_status"])
        ordinal = resulting_ordinal
        cursor = max(cursor, at)

    if cursor < ended_at:
        interval = _seconds(cursor, ended_at)
        if state == "running" and ordinal in segment_ordinals:
            active_by_ordinal[ordinal] += interval
        elif state == "paused":
            paused_seconds += interval

    wall_seconds = _seconds(started_at, ended_at)
    total_active_seconds = sum(active_by_ordinal.values())
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_seconds": wall_seconds,
        "active_seconds": total_active_seconds,
        "paused_seconds": paused_seconds,
        "active_by_ordinal": active_by_ordinal,
        "visits": visits,
        "first_entry": first_entry,
        "action_counts": action_counts,
    }


def _readiness_analysis(con, user_id: str, run_id: str, segments: list[dict], timeline: dict) -> dict:
    final_rows = con.execute(
        """SELECT ordinal,cue_ready,scene_ready,assets_ready,revision,updated_at
           FROM live_show_run_readiness
           WHERE user_id=? AND run_id=?""",
        (user_id, run_id),
    ).fetchall()
    final = {
        int(row["ordinal"]): {
            "cue_ready": bool(row["cue_ready"]),
            "scene_ready": bool(row["scene_ready"]),
            "assets_ready": bool(row["assets_ready"]),
            "all_ready": bool(row["cue_ready"] and row["scene_ready"] and row["assets_ready"]),
            "revision": int(row["revision"]),
            "updated_at": row["updated_at"],
        }
        for row in final_rows
    }

    commands = _readiness_commands(con, user_id, run_id)
    state: dict[int, dict[str, bool]] = defaultdict(
        lambda: {"cue_ready": False, "scene_ready": False, "assets_ready": False}
    )
    before_entry: dict[int, bool | None] = {1: None}
    command_index = 0
    ordered_entries = sorted(
        (
            (ordinal, entered)
            for ordinal, entered in timeline["first_entry"].items()
            if ordinal != 1
        ),
        key=lambda item: item[1],
    )
    for ordinal, entered in ordered_entries:
        while command_index < len(commands):
            command = commands[command_index]
            at = _parse(command["created_at"])
            if at is None or at > entered:
                break
            target = int(command["ordinal"])
            for field, column in (
                ("cue_ready", "requested_cue_ready"),
                ("scene_ready", "requested_scene_ready"),
                ("assets_ready", "requested_assets_ready"),
            ):
                if command[column] is not None:
                    state[target][field] = bool(command[column])
            command_index += 1
        snapshot = state[ordinal]
        before_entry[ordinal] = all(snapshot.values())

    prepared_values = [value for value in before_entry.values() if value is not None]
    return {
        "final": final,
        "before_entry": before_entry,
        "prepared_before_entry": sum(1 for value in prepared_values if value),
        "eligible_before_entry": len(prepared_values),
        "update_count": len(commands),
    }


def _emergency_analysis(con, user_id: str, started_at: datetime, ended_at: datetime) -> dict:
    rows = con.execute(
        """SELECT requested_mode,created_at
           FROM live_overlay_emergency_commands
           WHERE user_id=? ORDER BY created_at,rowid""",
        (user_id,),
    ).fetchall()
    modes: dict[str, int] = defaultdict(int)
    total = 0
    for row in rows:
        at = _parse(row["created_at"])
        if at is None or at < started_at or at > ended_at:
            continue
        modes[str(row["requested_mode"])] += 1
        total += 1
    return {
        "total": total,
        "safe_hold": modes["safe_hold"],
        "automation_pause": modes["automation_pause"],
        "normal": modes["normal"],
        "scope": "Aura-local emergency controls only",
    }


def _guidance(report: dict) -> list[str]:
    guidance: list[str] = []
    if report["status"] == "aborted":
        guidance.append("This Aura Show Runner session was aborted; review where the run stopped before reusing the plan.")
    elif not report["completion"]["finished_on_final_segment"]:
        guidance.append("The runner was completed before the final planned segment; review whether the later segments are still needed.")

    if report["timing"]["active_variance_seconds"] > 180:
        guidance.append("The show ran more than three active minutes over plan; shorten flexible segments or increase their planned durations.")
    elif report["timing"]["active_variance_seconds"] < -180 and report["status"] == "completed":
        guidance.append("The show finished more than three active minutes under plan; check whether planned segments were intentionally shortened or skipped.")

    over = [segment for segment in report["segments"] if segment["variance_seconds"] > 120]
    if over:
        names = ", ".join(str(segment["title"]) for segment in over[:3])
        guidance.append(f"Segments materially over plan: {names}.")

    readiness = report["readiness"]
    if readiness["eligible_before_entry"] and readiness["prepared_before_entry"] < readiness["eligible_before_entry"]:
        guidance.append("Some later segments were entered before scene, cue and assets were all marked ready; prepare those checks earlier next time.")

    if report["emergency_controls"]["safe_hold"]:
        guidance.append("Safe Hold was used during this run. Review the Aura-local production issue before the next session; provider LIVE control was not implied.")

    if not guidance:
        guidance.append("The audited run-of-show completed without a major operational variance detected by Aura.")
    return guidance


def build_report(con, row) -> dict:
    user_id = str(row["user_id"])
    run_id = str(row["id"])
    status = str(row["status"])
    if status not in TERMINAL_STATUSES:
        raise HTTPException(409, "Post-show reports are available only for completed or aborted Show Runner sessions")

    raw_segments = run_engine._segments(con, user_id, run_id)
    segment_ordinals = {int(segment["ordinal"]) for segment in raw_segments}
    commands = _run_commands(con, user_id, run_id)
    timeline = _timeline(row, commands, segment_ordinals)
    readiness = _readiness_analysis(con, user_id, run_id, raw_segments, timeline)
    total_planned = sum(int(segment["duration_seconds"]) for segment in raw_segments)

    segments: list[dict] = []
    for segment in raw_segments:
        ordinal = int(segment["ordinal"])
        actual = round(float(timeline["active_by_ordinal"].get(ordinal, 0.0)), 3)
        planned = int(segment["duration_seconds"])
        final_ready = readiness["final"].get(
            ordinal,
            {
                "cue_ready": False,
                "scene_ready": False,
                "assets_ready": False,
                "all_ready": False,
                "revision": 0,
                "updated_at": None,
            },
        )
        segments.append(
            {
                "ordinal": ordinal,
                "title": segment["title"],
                "segment_type": segment["segment_type"],
                "scene_name": segment["scene_name"],
                "planned_seconds": planned,
                "active_seconds": actual,
                "variance_seconds": round(actual - planned, 3),
                "visit_count": int(timeline["visits"].get(ordinal, 0)),
                "first_entered_at": (
                    timeline["first_entry"].get(ordinal).isoformat()
                    if timeline["first_entry"].get(ordinal)
                    else None
                ),
                "ready_before_first_entry": readiness["before_entry"].get(ordinal),
                "final_readiness": final_ready,
            }
        )

    current_ordinal = int(row["current_ordinal"])
    visited_count = sum(1 for segment in segments if segment["visit_count"] > 0)
    emergency = _emergency_analysis(
        con,
        user_id,
        timeline["started_at"],
        timeline["ended_at"],
    )
    action_counts = {action: int(timeline["action_counts"].get(action, 0)) for action in sorted(run_engine.RUN_ACTIONS)}
    report = {
        "run_id": run_id,
        "plan_id": row["plan_id"],
        "plan_revision": int(row["plan_revision"]),
        "title": row["title"],
        "status": status,
        "started_at": timeline["started_at"].isoformat(),
        "ended_at": timeline["ended_at"].isoformat(),
        "timing": {
            "planned_seconds": total_planned,
            "active_seconds": round(timeline["active_seconds"], 3),
            "paused_seconds": round(timeline["paused_seconds"], 3),
            "wall_seconds": round(timeline["wall_seconds"], 3),
            "active_variance_seconds": round(timeline["active_seconds"] - total_planned, 3),
        },
        "completion": {
            "ended_on_ordinal": current_ordinal,
            "final_planned_ordinal": len(raw_segments),
            "finished_on_final_segment": bool(raw_segments) and current_ordinal == len(raw_segments),
            "segments_visited": visited_count,
            "segments_planned": len(raw_segments),
        },
        "commands": action_counts,
        "readiness": {
            "prepared_before_entry": int(readiness["prepared_before_entry"]),
            "eligible_before_entry": int(readiness["eligible_before_entry"]),
            "updates": int(readiness["update_count"]),
        },
        "emergency_controls": emergency,
        "segments": segments,
        "provider_metrics_available": False,
        "metrics_scope": "Aura Command Center run-of-show operations only",
        "provider_write_authority": False,
        "provider_live_controlled": False,
        "guardian_safeguarding_escalation_preserved": True,
        "privacy_boundary": (
            "This report is derived from run structure, timing, readiness flags and Aura-local control audit events. "
            "It does not expose Auto Cue script text or private cue labels and does not fabricate provider viewer, gift, like, share or diamond metrics."
        ),
    }
    report["guidance"] = _guidance(report)
    return report


@router.get("/api/live-show/reports")
def list_reports(request: Request, limit: int = Query(default=20, ge=1, le=50)):
    member = _member(request)
    with _connect() as con:
        rows = con.execute(
            """SELECT * FROM live_show_runs
               WHERE user_id=? AND status IN ('completed','aborted')
               ORDER BY ended_at DESC,started_at DESC LIMIT ?""",
            (member.user_id, int(limit)),
        ).fetchall()
        reports = [build_report(con, row) for row in rows]
    return JSONResponse(
        {"reports": reports},
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/api/live-show/runs/{run_id}/report")
def get_report(run_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        row = run_engine._run_row(con, member.user_id, run_id)
        report = build_report(con, row)
    return JSONResponse(
        report,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay-studio/post-show", response_class=HTMLResponse, include_in_schema=False)
def post_show_page(request: Request):
    member = _member(request)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    product = escape(PRODUCT_FULL_NAME)
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow"><title>LIVE Post-Show Report — __PRODUCT__</title><style>
:root{--bg:#070812;--card:#111526;--line:#ffffff20;--muted:#bdc6d8;--gold:#efc96b;--violet:#9b72ff;--good:#80e6b1;--warn:#ffd37a;--danger:#ff8ca6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1160px,calc(100% - 28px));margin:auto;padding:30px 0 70px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin:14px 0}.metric{font-size:1.8rem;font-weight:900}.muted{color:var(--muted);line-height:1.55}.btn,select{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px}.btn{cursor:pointer;font-weight:850;text-decoration:none;display:inline-block}.segment{border-top:1px solid #ffffff15;padding:12px 0}.good{color:var(--good)}.warn{color:var(--warn)}.danger{color:var(--danger)}@media(max-width:820px){.grid{grid-template-columns:1fr}}</style></head><body><main class="wrap"><p><a class="btn" href="/live-overlay-studio/live-director">← LIVE Director</a> <a class="btn" href="/live-overlay-studio/show-runner">Show Runner</a> <a class="btn" href="/live-overlay-studio/orchestration">Overlay Orchestration</a> <a class="btn" href="/live-guardian">LIVE Guardian</a></p><div style="color:var(--gold);font-weight:900">Powered by Aura AI · Creator LIVE Production</div><h1>LIVE Post-Show Report</h1><p class="muted">Welcome __DISPLAY_NAME__. Review what the audited Aura run-of-show actually did after a completed or aborted session. These are operational metrics only: Aura does not invent TikTok viewers, likes, gifts, shares or diamonds.</p><section class="card"><label for="run">Recent ended Show Runner session</label><br><select id="run" style="margin-top:8px;min-width:min(100%,520px)"></select></section><div id="app" class="card muted">Loading reports…</div><section class="card"><b>Privacy and provider boundary</b><p class="muted">This page never exposes spoken Auto Cue script text or private cue labels. It reports Aura-local timing, readiness and controls only. Completing a Show Runner is not proof that the provider LIVE ended, and no provider write authority is claimed. LIVE Guardian safeguarding remains independent and preserved.</p></section></main><script>
const $=id=>document.getElementById(id);const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');const fmt=s=>{s=Math.max(0,Math.round(Number(s||0)));const m=Math.floor(s/60),x=s%60;return `${m}:${String(x).padStart(2,'0')}`};let reports=[];async function load(){const r=await fetch('/api/live-show/reports',{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Request failed');reports=d.reports||[];renderPicker()}function renderPicker(){if(!reports.length){$('run').innerHTML='<option>No ended runs yet</option>';$('run').disabled=true;$('app').textContent='Complete or abort a Show Runner session to create an audited post-show report.';return}$('run').innerHTML=reports.map((x,i)=>`<option value="${i}">${esc(x.title)} · ${esc(x.status)} · ${esc(x.ended_at)}</option>`).join('');$('run').onchange=()=>render(reports[Number($('run').value)]);render(reports[0])}function cls(v){return v>120?'danger':v<-120?'warn':'good'}function render(d){const t=d.timing,c=d.completion,ready=d.readiness;$('app').className='';$('app').innerHTML=`<div class="grid"><section class="card"><div class="muted">Active time</div><div class="metric">${fmt(t.active_seconds)}</div><div class="muted">Planned ${fmt(t.planned_seconds)} · variance <span class="${cls(t.active_variance_seconds)}">${t.active_variance_seconds>0?'+':''}${Math.round(t.active_variance_seconds)}s</span></div></section><section class="card"><div class="muted">Paused time</div><div class="metric">${fmt(t.paused_seconds)}</div><div class="muted">Wall time ${fmt(t.wall_seconds)}</div></section><section class="card"><div class="muted">Segments visited</div><div class="metric">${c.segments_visited}/${c.segments_planned}</div><div class="muted">Ended on segment ${c.ended_on_ordinal}</div></section></div><section class="card"><h2>${esc(d.title)}</h2><p class="muted">Runner status: <b>${esc(d.status)}</b> · prepared later segments before entry: ${ready.prepared_before_entry}/${ready.eligible_before_entry} · readiness updates: ${ready.updates} · Safe Hold uses: ${d.emergency_controls.safe_hold}</p>${d.segments.map(s=>`<div class="segment"><b>${s.ordinal}. ${esc(s.title)}</b> <span class="muted">${esc(s.segment_type)}</span><br><span class="muted">Active ${fmt(s.active_seconds)} / planned ${fmt(s.planned_seconds)} · variance <span class="${cls(s.variance_seconds)}">${s.variance_seconds>0?'+':''}${Math.round(s.variance_seconds)}s</span> · visits ${s.visit_count}${s.ready_before_first_entry===null?'':` · ready before entry ${s.ready_before_first_entry?'yes':'no'}`}</span></div>`).join('')}</section><section class="card"><h2>Aura operational guidance</h2>${d.guidance.map(x=>`<p>• ${esc(x)}</p>`).join('')}<p class="muted"><b>Provider metrics:</b> not connected to this report. No viewer, like, gift, share or diamond value is inferred.</p></section>`}load().catch(e=>$('app').textContent=e.message);
</script></body></html>'''.replace("__PRODUCT__", product).replace("__DISPLAY_NAME__", display_name)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = ["router", "build_report", "list_reports", "get_report", "post_show_page"]
