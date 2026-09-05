from __future__ import annotations

from collections import defaultdict
from html import escape

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import aura_live_post_show_report as post_show
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Trend Coach"])
MATERIAL_OVERRUN_SECONDS = 120.0


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _rate(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _readiness_rate(report: dict) -> float | None:
    readiness = report["readiness"]
    return _rate(
        float(readiness["prepared_before_entry"]),
        float(readiness["eligible_before_entry"]),
    )


def _rework_count(report: dict) -> int:
    commands = report.get("commands") or {}
    return int(commands.get("previous", 0)) + int(commands.get("jump", 0))


def _session_snapshot(report: dict) -> dict:
    return {
        "run_id": report["run_id"],
        "title": report["title"],
        "status": report["status"],
        "ended_at": report["ended_at"],
        "active_seconds": report["timing"]["active_seconds"],
        "planned_seconds": report["timing"]["planned_seconds"],
        "active_variance_seconds": report["timing"]["active_variance_seconds"],
        "paused_seconds": report["timing"]["paused_seconds"],
        "finished_on_final_segment": report["completion"]["finished_on_final_segment"],
        "readiness_rate": _readiness_rate(report),
        "safe_hold_count": int(report["emergency_controls"]["safe_hold"]),
        "rework_count": _rework_count(report),
    }


def _latest_comparison(items: list[dict]) -> dict | None:
    if len(items) < 2:
        return None
    latest = _session_snapshot(items[0])
    prior = _session_snapshot(items[1])

    latest_ready = latest["readiness_rate"]
    prior_ready = prior["readiness_rate"]
    readiness_delta = None
    if latest_ready is not None and prior_ready is not None:
        readiness_delta = round(latest_ready - prior_ready, 4)

    return {
        "latest_run_id": latest["run_id"],
        "prior_run_id": prior["run_id"],
        "status_changed": latest["status"] != prior["status"],
        "active_variance_delta_seconds": round(
            float(latest["active_variance_seconds"]) - float(prior["active_variance_seconds"]),
            3,
        ),
        "absolute_variance_improved": abs(float(latest["active_variance_seconds"]))
        < abs(float(prior["active_variance_seconds"])),
        "paused_seconds_delta": round(float(latest["paused_seconds"]) - float(prior["paused_seconds"]), 3),
        "readiness_rate_delta": readiness_delta,
        "safe_hold_delta": int(latest["safe_hold_count"]) - int(prior["safe_hold_count"]),
        "rework_delta": int(latest["rework_count"]) - int(prior["rework_count"]),
    }


def _segment_patterns(items: list[dict]) -> list[dict]:
    patterns: dict[tuple[str, str], dict] = {}
    for report in items:
        seen_in_run: set[tuple[str, str]] = set()
        overrun_in_run: set[tuple[str, str]] = set()
        for segment in report.get("segments") or []:
            title = " ".join(str(segment.get("title") or "Untitled segment").split())[:160]
            segment_type = " ".join(str(segment.get("segment_type") or "segment").split())[:80]
            key = (segment_type.casefold(), title.casefold())
            entry = patterns.setdefault(
                key,
                {
                    "title": title,
                    "segment_type": segment_type,
                    "sessions_seen": 0,
                    "overrun_sessions": 0,
                    "variances": [],
                },
            )
            if key not in seen_in_run:
                entry["sessions_seen"] += 1
                seen_in_run.add(key)
            variance = float(segment.get("variance_seconds") or 0.0)
            entry["variances"].append(variance)
            if variance > MATERIAL_OVERRUN_SECONDS and key not in overrun_in_run:
                entry["overrun_sessions"] += 1
                overrun_in_run.add(key)

    recurring = []
    for value in patterns.values():
        if int(value["overrun_sessions"]) < 2:
            continue
        recurring.append(
            {
                "title": value["title"],
                "segment_type": value["segment_type"],
                "sessions_seen": int(value["sessions_seen"]),
                "overrun_sessions": int(value["overrun_sessions"]),
                "overrun_rate": _rate(value["overrun_sessions"], value["sessions_seen"]),
                "average_variance_seconds": _average([float(v) for v in value["variances"]]),
            }
        )
    recurring.sort(key=lambda item: (item["overrun_sessions"], item["average_variance_seconds"]), reverse=True)
    return recurring[:8]


def _guidance(payload: dict) -> list[str]:
    if payload["sessions"]["count"] == 0:
        return ["Complete an Aura Show Runner session to establish a cross-session operational baseline."]

    guidance: list[str] = []
    sessions = payload["sessions"]
    timing = payload["timing"]
    readiness = payload["readiness"]
    controls = payload["controls"]
    latest = payload.get("latest")
    comparison = payload.get("latest_vs_prior")

    if latest and latest["status"] == "aborted":
        guidance.append("The latest audited run was aborted. Review the stop point and production conditions before reusing that show plan.")
    elif sessions["completion_rate"] is not None and sessions["completion_rate"] < 0.75:
        guidance.append("Fewer than three quarters of the audited runs completed; simplify the run-of-show or resolve recurring production blockers.")

    if timing["average_absolute_active_variance_seconds"] > 180:
        guidance.append("Average run timing is more than three active minutes away from plan; rebalance flexible segment durations before the next LIVE.")

    if readiness["rate"] is not None and readiness["rate"] < 0.9:
        guidance.append("Scene, cue and asset readiness is not consistently complete before later segment entry; move those checks earlier in the workflow.")

    if controls["safe_hold_total"] > 0:
        guidance.append("Safe Hold appears in the audited history. Review the Aura-local production cause; this does not imply TikTok LIVE control or a provider incident.")

    if controls["average_rework_commands_per_session"] > 1:
        guidance.append("Previous/jump navigation is recurring. Tighten show structure or transitions to reduce in-session rework without rushing safety-critical cues.")

    patterns = payload["recurring_segment_overruns"]
    if patterns:
        labels = ", ".join(item["title"] for item in patterns[:3])
        guidance.append(f"Recurring segment overruns detected in: {labels}. Consider increasing those planned durations or tightening their format.")

    if comparison:
        improvements: list[str] = []
        if comparison["absolute_variance_improved"]:
            improvements.append("timing variance")
        if comparison["readiness_rate_delta"] is not None and comparison["readiness_rate_delta"] > 0:
            improvements.append("pre-entry readiness")
        if comparison["rework_delta"] < 0:
            improvements.append("navigation/rework")
        if improvements:
            guidance.append("Latest-vs-prior improvement: " + ", ".join(improvements) + ". Keep the changes that produced that operational gain.")

    if not guidance:
        guidance.append("Cross-session Aura run-of-show consistency is stable with no major operational trend requiring intervention.")
    return guidance


def build_trends(items: list[dict]) -> dict:
    completed = sum(1 for item in items if item["status"] == "completed")
    aborted = sum(1 for item in items if item["status"] == "aborted")
    active_variances = [float(item["timing"]["active_variance_seconds"]) for item in items]
    paused = [float(item["timing"]["paused_seconds"]) for item in items]
    active = [float(item["timing"]["active_seconds"]) for item in items]
    planned = [float(item["timing"]["planned_seconds"]) for item in items]

    prepared = sum(int(item["readiness"]["prepared_before_entry"]) for item in items)
    eligible = sum(int(item["readiness"]["eligible_before_entry"]) for item in items)
    safe_holds = sum(int(item["emergency_controls"]["safe_hold"]) for item in items)
    automation_pauses = sum(int(item["emergency_controls"]["automation_pause"]) for item in items)
    rework_counts = [_rework_count(item) for item in items]

    payload = {
        "sessions": {
            "count": len(items),
            "completed": completed,
            "aborted": aborted,
            "completion_rate": _rate(completed, len(items)),
        },
        "timing": {
            "average_planned_seconds": _average(planned),
            "average_active_seconds": _average(active),
            "average_paused_seconds": _average(paused),
            "average_active_variance_seconds": _average(active_variances),
            "average_absolute_active_variance_seconds": _average([abs(value) for value in active_variances]),
        },
        "readiness": {
            "prepared_before_entry": prepared,
            "eligible_before_entry": eligible,
            "rate": _rate(prepared, eligible),
        },
        "controls": {
            "safe_hold_total": safe_holds,
            "automation_pause_total": automation_pauses,
            "average_safe_holds_per_session": _average([float(item["emergency_controls"]["safe_hold"]) for item in items]),
            "rework_commands_total": sum(rework_counts),
            "average_rework_commands_per_session": _average([float(value) for value in rework_counts]),
            "scope": "Aura-local run-of-show and emergency control audit only",
        },
        "latest": _session_snapshot(items[0]) if items else None,
        "latest_vs_prior": _latest_comparison(items),
        "recurring_segment_overruns": _segment_patterns(items),
        "provider_metrics_available": False,
        "metrics_scope": "Aura Command Center cross-session run-of-show operations only",
        "provider_write_authority": False,
        "provider_live_controlled": False,
        "guardian_safeguarding_escalation_preserved": True,
        "privacy_boundary": (
            "Trend Coach aggregates audited show structure, timing, readiness flags, navigation and Aura-local emergency control events only. "
            "It never exposes Auto Cue script text or private cue labels and never fabricates TikTok viewers, gifts, likes, shares, coins or diamonds."
        ),
    }
    payload["guidance"] = _guidance(payload)
    return payload


def _load_trends(user_id: str, limit: int) -> dict:
    with post_show._connect() as con:
        rows = con.execute(
            """SELECT * FROM live_show_runs
               WHERE user_id=? AND status IN ('completed','aborted')
               ORDER BY ended_at DESC,started_at DESC LIMIT ?""",
            (user_id, int(limit)),
        ).fetchall()
        items = [post_show.build_report(con, row) for row in rows]
    payload = build_trends(items)
    payload["window"] = {"requested_limit": int(limit), "sessions_included": len(items)}
    return payload


@router.get("/api/live-show/trends")
def live_show_trends(request: Request, limit: int = Query(default=12, ge=2, le=50)):
    member = _member(request)
    return JSONResponse(
        _load_trends(member.user_id, int(limit)),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/live-overlay-studio/trends", response_class=HTMLResponse, include_in_schema=False)
def trend_coach_page(request: Request):
    member = _member(request)
    display_name = escape(getattr(member, "display_name", "Creator") or "Creator")
    product = escape(PRODUCT_FULL_NAME)
    page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="robots" content="noindex,nofollow"><title>Aura LIVE Trend Coach — __PRODUCT__</title><style>
:root{--bg:#050711;--panel:#101526;--line:#ffffff20;--gold:#efca6d;--cyan:#5de1ff;--violet:#9b72ff;--green:#7cdda7;--red:#ff92a8;--muted:#bcc5d7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#35145b77,transparent 30%),radial-gradient(circle at 90% 0,#123b5977,transparent 28%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1240px,calc(100% - 26px));margin:auto;padding:30px 0 60px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.btn{display:inline-block;text-decoration:none;color:#fff;border:1px solid var(--line);border-radius:11px;padding:9px 12px;font-weight:850;background:#ffffff08}.primary{border:0;background:linear-gradient(120deg,var(--gold),var(--violet));color:#170c20}.hero{padding:42px 0 22px}.eyebrow{color:var(--gold);font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;font-weight:900}h1{font-size:clamp(2.6rem,7vw,5.5rem);line-height:.92;letter-spacing:-.05em;margin:.16em 0}.lead,.muted{color:var(--muted);line-height:1.6}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#11172aec,#090d19ec);padding:16px}.metric{font-size:1.75rem;font-weight:950;color:var(--gold)}.section{margin-top:18px}.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}.guidance{border-left:3px solid var(--cyan);padding:10px 12px;margin:8px 0;background:#5de1ff0b;border-radius:8px}.pattern{border:1px solid var(--line);border-radius:12px;padding:11px;margin:8px 0;background:#ffffff05}.truth{border-color:#7cdda744;background:#7cdda70b}.bad{color:var(--red)}.good{color:var(--green)}@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}}@media(max-width:520px){.grid{grid-template-columns:1fr}}
</style></head><body><main class="wrap"><header class="top"><div><b>__PRODUCT__</b><div class="eyebrow">Private LIVE operations</div></div><nav><a class="btn" href="/live-overlay-studio">LIVE Studio</a> <a class="btn" href="/live-overlay-studio/show-runner">Show Runner</a> <a class="btn" href="/live-overlay-studio/post-show">Post-Show Reports</a></nav></header><section class="hero"><div class="eyebrow">Aura LIVE Cross-Session Trend Coach</div><h1>Operational patterns, not invented provider metrics.</h1><p class="lead">__DISPLAY__, Trend Coach compares your audited Aura Show Runner sessions to surface timing, readiness, rework and safety-control patterns across LIVE productions.</p></section><section id="state" class="card">Loading audited run history…</section><section id="metrics" class="grid section"></section><section class="two section"><div class="card"><div class="eyebrow">Aura coaching</div><h2>What to improve next</h2><div id="guidance"></div></div><div class="card"><div class="eyebrow">Recurring overruns</div><h2>Segments that repeatedly exceed plan</h2><div id="patterns" class="muted">Loading…</div></div></section><section class="card truth section"><div class="eyebrow">Truth & privacy boundary</div><p id="privacy" class="muted"></p><p class="muted"><b>LIVE Guardian remains independent and preserved.</b> Trend Coach is read-only and has no provider write authority or TikTok LIVE control.</p></section></main><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pct=v=>v==null?'—':Math.round(v*100)+'%';const mins=v=>(Number(v||0)/60).toFixed(1)+'m';
async function boot(){try{const r=await fetch('/api/live-show/trends?limit=12',{credentials:'same-origin'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Unable to load trends');document.getElementById('state').innerHTML=`<b>${d.sessions.count} audited session${d.sessions.count===1?'':'s'}</b><div class="muted">${d.sessions.completed} completed · ${d.sessions.aborted} aborted · ${pct(d.sessions.completion_rate)} completion rate</div>`;const metrics=[['Average active time',mins(d.timing.average_active_seconds)],['Average |variance|',mins(d.timing.average_absolute_active_variance_seconds)],['Pre-entry readiness',pct(d.readiness.rate)],['Avg rework/session',Number(d.controls.average_rework_commands_per_session).toFixed(1)],['Safe Holds',d.controls.safe_hold_total],['Average pause',mins(d.timing.average_paused_seconds)],['Completed',d.sessions.completed],['Aborted',d.sessions.aborted]];document.getElementById('metrics').innerHTML=metrics.map(([k,v])=>`<div class="card"><div class="eyebrow">${esc(k)}</div><div class="metric">${esc(v)}</div></div>`).join('');document.getElementById('guidance').innerHTML=d.guidance.map(x=>`<div class="guidance">${esc(x)}</div>`).join('');document.getElementById('patterns').innerHTML=d.recurring_segment_overruns.length?d.recurring_segment_overruns.map(x=>`<div class="pattern"><b>${esc(x.title)}</b><div class="muted">${esc(x.segment_type)} · over plan in ${x.overrun_sessions}/${x.sessions_seen} audited sessions · avg variance ${mins(x.average_variance_seconds)}</div></div>`).join(''):'No segment has exceeded plan by more than two minutes in at least two audited sessions.';document.getElementById('privacy').textContent=d.privacy_boundary;}catch(e){document.getElementById('state').innerHTML=`<b class="bad">Trend Coach unavailable</b><div class="muted">${esc(e.message)}</div>`;}}
boot();
</script></body></html>'''
    page = page.replace("__PRODUCT__", product).replace("__DISPLAY__", display_name)
    return HTMLResponse(
        page,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


__all__ = ["router", "build_trends"]
