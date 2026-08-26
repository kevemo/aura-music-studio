from __future__ import annotations

import json
import sqlite3
from html import escape
from statistics import mean

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .owner_identity import owner_session_authorized
from .owner_user_control import OwnerUserControl

router = APIRouter(tags=["ESP Owner Focus Dashboard"])

LIVE_KEYS = ("diamonds", "duration_minutes", "avg_watch_seconds", "new_followers", "shares")
VIDEO_KEYS = ("views", "completion_rate", "shares", "saves", "new_followers")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_change(current: float, previous: float) -> float:
    if previous == 0:
        if current == 0:
            return 0.0
        return 100.0 if current > 0 else -100.0
    value = (current - previous) / abs(previous) * 100.0
    return max(-200.0, min(200.0, value))


class OwnerFocusService:
    """Explainable owner visibility; never an automatic disciplinary engine."""

    def __init__(self, control: OwnerUserControl | None = None):
        self.control = control or OwnerUserControl()
        self.db_path = self.control.db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    def _decode_metrics(value: str | None) -> dict:
        try:
            data = json.loads(value or "{}")
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _latest_pair(self, con: sqlite3.Connection, user_id: str, kind: str):
        rows = con.execute(
            """SELECT metrics_json,period_label,created_at FROM esp_performance_submissions
               WHERE user_id=? AND kind=? ORDER BY created_at DESC LIMIT 2""",
            (user_id, kind),
        ).fetchall()
        return rows if len(rows) == 2 else []

    def _kind_delta(self, rows, keys: tuple[str, ...]) -> tuple[list[float], list[dict]]:
        if len(rows) != 2:
            return [], []
        current = self._decode_metrics(rows[0]["metrics_json"])
        previous = self._decode_metrics(rows[1]["metrics_json"])
        changes: list[float] = []
        details: list[dict] = []
        for key in keys:
            now = _number(current.get(key))
            before = _number(previous.get(key))
            if now is None or before is None:
                continue
            delta = round(_percent_change(now, before), 1)
            changes.append(delta)
            details.append({"metric": key, "current": now, "previous": before, "change_percent": delta})
        return changes, details

    def creator_momentum(self) -> list[dict]:
        creators = [
            row for row in self.control.list_users()
            if row.get("esp_status") in {"active", "owner"} and row.get("esp_roles") in {"creator", "both"}
        ]
        result: list[dict] = []
        with self._connect() as con:
            for creator in creators:
                changes: list[float] = []
                details: list[dict] = []
                for kind, keys in (("live", LIVE_KEYS), ("video", VIDEO_KEYS)):
                    kind_changes, kind_details = self._kind_delta(self._latest_pair(con, creator["id"], kind), keys)
                    changes.extend(kind_changes)
                    for item in kind_details:
                        item["kind"] = kind
                        details.append(item)
                if not changes:
                    state, score = "insufficient_data", None
                else:
                    score = round(mean(changes), 1)
                    state = "rising" if score >= 10 else "dropping" if score <= -10 else "plateau"
                result.append({
                    "user_id": creator["id"],
                    "display_name": creator.get("display_name") or creator.get("tiktok_handle") or creator["id"],
                    "tiktok_handle": creator.get("tiktok_handle") or "",
                    "niche": creator.get("niche") or "",
                    "mentor": creator.get("mentor") or "",
                    "state": state,
                    "momentum_score": score,
                    "comparisons": details,
                    "progress_submissions": int(creator.get("progress_submissions") or 0),
                    "last_progress_at": creator.get("last_progress_at"),
                })
        order = {"dropping": 0, "plateau": 1, "rising": 2, "insufficient_data": 3}
        result.sort(key=lambda row: (order[row["state"]], row["momentum_score"] if row["momentum_score"] is not None else 999))
        return result

    def agent_funnels(self) -> list[dict]:
        with self._connect() as con:
            agents = con.execute(
                """SELECT u.id,u.display_name,e.tiktok_handle,e.region
                   FROM esp_memberships e JOIN users u ON u.id=e.user_id
                   WHERE e.status IN ('active','owner') AND (e.roles IN ('agent','both') OR e.status='owner')
                   ORDER BY u.display_name"""
            ).fetchall()
            result: list[dict] = []
            for agent in agents:
                counts = {
                    row["pipeline_status"]: int(row["n"])
                    for row in con.execute(
                        """SELECT pipeline_status,COUNT(*) n FROM esp_creator_discovery_leads
                           WHERE assigned_agent_user_id=? GROUP BY pipeline_status""",
                        (agent["id"],),
                    ).fetchall()
                }
                assigned = con.execute(
                    """SELECT COUNT(*) n FROM esp_agent_creator_assignments
                       WHERE agent_user_id=? AND status='active'""",
                    (agent["id"],),
                ).fetchone()
                open_checkins = con.execute(
                    "SELECT COUNT(*) n FROM esp_agent_checkins WHERE agent_user_id=? AND status='open'",
                    (agent["id"],),
                ).fetchone()
                contacted = sum(counts.get(key, 0) for key in ("contacted", "follow_up_due", "replied", "applied", "joined"))
                joined = counts.get("joined", 0)
                result.append({
                    "agent_user_id": agent["id"],
                    "display_name": agent["display_name"],
                    "tiktok_handle": agent["tiktok_handle"] or "",
                    "region": agent["region"] or "",
                    "active_assigned_creators": int(assigned["n"] or 0) if assigned else 0,
                    "discovery_total": sum(counts.values()),
                    "ready": counts.get("ready", 0),
                    "follow_up_due": counts.get("follow_up_due", 0),
                    "applied": counts.get("applied", 0),
                    "joined": joined,
                    "contacted_or_beyond": contacted,
                    "join_conversion_percent": round(joined / contacted * 100, 1) if contacted else 0.0,
                    "open_creator_checkins": int(open_checkins["n"] or 0) if open_checkins else 0,
                    "attention_signal": counts.get("follow_up_due", 0) > 0 or (contacted >= 5 and joined == 0),
                })
        result.sort(key=lambda row: (-int(row["attention_signal"]), -row["follow_up_due"], row["display_name"].lower()))
        return result

    def coverage(self) -> dict:
        with self._connect() as con:
            active = int(con.execute("SELECT COUNT(*) n FROM esp_memberships WHERE status IN ('active','owner')").fetchone()["n"] or 0)
            profiles = int(con.execute(
                """SELECT COUNT(DISTINCT e.user_id) n FROM esp_memberships e
                   JOIN esp_niche_profiles n ON n.user_id=e.user_id WHERE e.status IN ('active','owner')"""
            ).fetchone()["n"] or 0)
            creators = int(con.execute(
                """SELECT COUNT(*) n FROM esp_memberships WHERE status IN ('active','owner')
                   AND roles IN ('creator','both')"""
            ).fetchone()["n"] or 0)
            creators_with_progress = int(con.execute(
                """SELECT COUNT(DISTINCT e.user_id) n FROM esp_memberships e
                   JOIN esp_performance_submissions p ON p.user_id=e.user_id
                   WHERE e.status IN ('active','owner') AND e.roles IN ('creator','both')"""
            ).fetchone()["n"] or 0)
            learners = int(con.execute(
                """SELECT COUNT(DISTINCT e.user_id) n FROM esp_memberships e
                   JOIN esp_training_progress p ON p.user_id=e.user_id WHERE e.status IN ('active','owner')"""
            ).fetchone()["n"] or 0)
            lead_total = int(con.execute("SELECT COUNT(*) n FROM esp_creator_discovery_leads").fetchone()["n"] or 0)
            validated = int(con.execute(
                "SELECT COUNT(*) n FROM esp_creator_discovery_leads WHERE validation_status<>'unreviewed'"
            ).fetchone()["n"] or 0)
        pct = lambda numerator, denominator: round(numerator / denominator * 100, 1) if denominator else 100.0
        components = {
            "niche_profile_coverage": pct(profiles, active),
            "creator_progress_coverage": pct(creators_with_progress, creators),
            "training_participation": pct(learners, active),
            "discovery_validation_hygiene": pct(validated, lead_total),
        }
        score = round(mean(components.values()), 1)
        return {
            "active_esp_members": active,
            "active_creators": creators,
            "components": components,
            "system_health_score": score,
            "formula": "equal_weight_mean_of_four_visible_coverage_components",
            "automatic_penalties": False,
        }

    def dashboard(self) -> dict:
        creators = self.creator_momentum()
        agents = self.agent_funnels()
        coverage = self.coverage()
        summary = self.control.dashboard_summary()
        dropping = [row for row in creators if row["state"] == "dropping"][:5]
        rising = sorted(
            [row for row in creators if row["state"] == "rising"],
            key=lambda row: row["momentum_score"] or 0,
            reverse=True,
        )[:5]
        agent_attention = [row for row in agents if row["attention_signal"]][:5]
        ready_leads = sum(row["ready"] for row in agents)
        return {
            "network": summary,
            "momentum": {
                "rising": rising,
                "dropping": dropping,
                "plateau": [row for row in creators if row["state"] == "plateau"],
                "insufficient_data": [row for row in creators if row["state"] == "insufficient_data"],
                "all": creators,
            },
            "agents": agents,
            "coverage": coverage,
            "focus": {
                "dropping_creators": dropping,
                "agent_follow_up_attention": agent_attention,
                "growth_opportunities": {
                    "rising_creators": rising,
                    "validated_recruitment_leads_ready": ready_leads,
                },
            },
            "human_review_required": True,
            "automatic_discipline": False,
            "momentum_method": "mean percentage change across comparable LIVE/video metrics from the two latest submissions of the same type; >=10 rising, <=-10 dropping, otherwise plateau",
        }


service = OwnerFocusService()


def _allowed(request: Request) -> bool:
    return owner_session_authorized(request)


@router.get("/owner/api/focus")
def owner_focus_api(request: Request):
    if not _allowed(request):
        raise PermissionError("Owner session required")
    return service.dashboard()


def _creator_list(rows: list[dict], empty: str) -> str:
    if not rows:
        return f"<p class='muted'>{escape(empty)}</p>"
    return "".join(
        f"<div class='item'><b>{escape(str(row['display_name']))}</b><span>{escape(str(row['state']).replace('_',' ').title())} · {escape(str(row['momentum_score'] if row['momentum_score'] is not None else '—'))}%</span><small>@{escape(str(row.get('tiktok_handle') or ''))} · {escape(str(row.get('niche') or ''))}</small></div>"
        for row in rows
    )


@router.get("/owner/focus", response_class=HTMLResponse, include_in_schema=False)
def owner_focus_page(request: Request):
    if not _allowed(request):
        return RedirectResponse("/owner", status_code=303)
    data = service.dashboard()
    focus = data["focus"]
    coverage = data["coverage"]
    network = data["network"]
    agents = data["agents"]
    agent_html = "".join(
        f"<tr><td><b>{escape(str(row['display_name']))}</b></td><td>{row['active_assigned_creators']}</td><td>{row['ready']}</td><td>{row['follow_up_due']}</td><td>{row['applied']}</td><td>{row['joined']}</td><td>{row['join_conversion_percent']}%</td><td>{'<span class=\"warn\">Review</span>' if row['attention_signal'] else '<span class=\"good\">On track</span>'}</td></tr>"
        for row in agents
    ) or "<tr><td colspan='8' class='muted'>No active Agent accounts yet.</td></tr>"
    components = coverage["components"]
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Owner Focus</title><style>
    :root{{--line:#ffffff1e;--gold:#f1c86f;--violet:#9e70ff;--muted:#c2bfd0;--good:#77dda6;--warn:#ffc878;--bad:#ff91a5}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#43175f,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1400px,calc(100% - 28px));margin:auto;padding:35px 0 70px}}.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:9px;flex-wrap:wrap}}.eyebrow{{font-size:.68rem;color:var(--gold);font-weight:950;text-transform:uppercase;letter-spacing:.15em}}h1{{font-size:clamp(2.8rem,7vw,5.6rem);letter-spacing:-.06em;line-height:.93;margin:.12em 0}}p,.muted{{color:var(--muted);line-height:1.55}}.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}.metric,.card{{border:1px solid var(--line);border-radius:16px;padding:13px;background:#14101deb}}.metric b{{font-size:1.55rem;display:block}}.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:10px}}.item{{display:grid;grid-template-columns:1.3fr .7fr;gap:5px;padding:8px 0;border-bottom:1px solid var(--line)}}.item small{{grid-column:1/-1;color:var(--muted)}}.btn{{border:1px solid var(--line);border-radius:9px;padding:8px 10px;background:#ffffff08;font-weight:850}}.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:900px}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left}}th{{font-size:.65rem;color:#9d96a9;text-transform:uppercase}}@media(max-width:900px){{.metrics{{grid-template-columns:1fr 1fr}}.grid3{{grid-template-columns:1fr}}}}@media(max-width:480px){{.metrics{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Owner Control</div><h1>Focus & Momentum</h1><p>One explainable view of what needs human attention today. No score here triggers an automatic penalty or disciplinary action.</p></div><div><a class='btn' href='/owner/dashboard'>Owner Dashboard</a> <a class='btn' href='/command-center/member-hub'>ESP Member Hub</a></div></div><section class='metrics'><div class='metric'><span class='muted'>ESP creators</span><b>{network['esp_creators']}</b></div><div class='metric'><span class='muted'>ESP agents</span><b>{network['esp_agents']}</b></div><div class='metric'><span class='muted'>Progress submissions</span><b>{network['progress_submissions']}</b></div><div class='metric'><span class='muted'>Ready prospects</span><b>{focus['growth_opportunities']['validated_recruitment_leads_ready']}</b></div><div class='metric'><span class='muted'>System health</span><b>{coverage['system_health_score']}%</b></div></section><section class='grid3'><article class='card'><div class='eyebrow'>Priority · Creator support</div><h2>Dropping momentum</h2>{_creator_list(focus['dropping_creators'],'No creators currently have a comparable dropping signal.')}</article><article class='card'><div class='eyebrow'>Opportunity</div><h2>Rising creators</h2>{_creator_list(focus['growth_opportunities']['rising_creators'],'No comparable rising signal yet.')}</article><article class='card'><div class='eyebrow'>System execution</div><h2>Coverage</h2><p>Niche profiles: <b>{components['niche_profile_coverage']}%</b><br>Creator progress: <b>{components['creator_progress_coverage']}%</b><br>Training participation: <b>{components['training_participation']}%</b><br>Discovery validation hygiene: <b>{components['discovery_validation_hygiene']}%</b></p><p class='muted'>System health is the equal-weight mean of these four visible coverage measures.</p></article></section><section class='card'><div class='eyebrow'>Agent recruitment & creator-success visibility</div><h2>Agent funnel</h2><div class='scroll'><table><thead><tr><th>Agent</th><th>Assigned</th><th>Ready leads</th><th>Follow-up</th><th>Applied</th><th>Joined</th><th>Join conversion</th><th>Signal</th></tr></thead><tbody>{agent_html}</tbody></table></div></section><section class='card'><b>Momentum method</b><p class='muted'>{escape(data['momentum_method'])}. This is a prioritisation aid, not a judgement of creator or agent quality.</p></section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "OwnerFocusService", "service"]
