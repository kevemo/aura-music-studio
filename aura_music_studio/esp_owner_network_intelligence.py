from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .owner_identity import owner_session_authorized, owner_theme, request_owner_persona

router = APIRouter(tags=["ESP Owner Network Intelligence"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _freshness(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return "missing"
    age = max(0, int((_now() - dt).total_seconds() // 86400))
    if age <= 7:
        return "current"
    if age <= 14:
        return "update_due"
    return "stale"


class OwnerEspNetworkIntelligenceStore:
    """Aggregate ESP operational intelligence without exposing private creative payloads."""

    def __init__(self, db_path=None):
        self.db_path = db_path or AccountStore().db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _table_exists(con: sqlite3.Connection, table: str) -> bool:
        row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return row is not None

    @staticmethod
    def _scalar(con: sqlite3.Connection, sql: str, params=(), default=0):
        try:
            row = con.execute(sql, params).fetchone()
            if not row:
                return default
            return row[0] if row[0] is not None else default
        except sqlite3.OperationalError:
            return default

    def _roles(self, con: sqlite3.Connection) -> dict:
        result = {"creators": 0, "agents": 0, "both": 0, "owners": 0, "active_esp": 0}
        if not self._table_exists(con, "esp_memberships"):
            return result
        rows = con.execute(
            """SELECT status,roles,COUNT(*) n FROM esp_memberships
               WHERE status IN ('active','owner') GROUP BY status,roles"""
        ).fetchall()
        for row in rows:
            n = int(row["n"] or 0)
            result["active_esp"] += n
            if row["status"] == "owner":
                result["owners"] += n
                continue
            role = (row["roles"] or "").lower()
            if role == "creator":
                result["creators"] += n
            elif role == "agent":
                result["agents"] += n
            elif role == "both":
                result["both"] += n
                result["creators"] += n
                result["agents"] += n
        return result

    def _regions(self, con: sqlite3.Connection) -> list[dict]:
        if not self._table_exists(con, "esp_memberships"):
            return []
        rows = con.execute(
            """SELECT COALESCE(NULLIF(TRIM(region),''),'Unspecified') region,COUNT(*) n
               FROM esp_memberships WHERE status='active'
               GROUP BY COALESCE(NULLIF(TRIM(region),''),'Unspecified') ORDER BY n DESC,region"""
        ).fetchall()
        return [{"region": row["region"], "members": int(row["n"] or 0)} for row in rows]

    def _training(self, con: sqlite3.Connection) -> dict:
        result = {"records": 0, "learners": 0, "average_percent": 0.0, "completed_records": 0}
        if not self._table_exists(con, "esp_training_progress"):
            return result
        row = con.execute(
            """SELECT COUNT(*) records,COUNT(DISTINCT user_id) learners,
                      AVG(percent) avg_percent,SUM(CASE WHEN percent>=100 THEN 1 ELSE 0 END) completed
               FROM esp_training_progress"""
        ).fetchone()
        if row:
            result.update({
                "records": int(row["records"] or 0),
                "learners": int(row["learners"] or 0),
                "average_percent": round(float(row["avg_percent"] or 0), 1),
                "completed_records": int(row["completed"] or 0),
            })
        return result

    def _evidence(self, con: sqlite3.Connection) -> dict:
        result = {
            "records": 0,
            "creators_with_evidence": 0,
            "freshness": {"current": 0, "update_due": 0, "stale": 0, "missing": 0},
            "needs_update": [],
            "direct_backstage_access": False,
        }
        if not self._table_exists(con, "esp_memberships"):
            return result
        creators = con.execute(
            """SELECT m.user_id,u.display_name,m.tiktok_handle,m.region
               FROM esp_memberships m JOIN users u ON u.id=m.user_id
               WHERE m.status='active' AND m.roles IN ('creator','both')
               ORDER BY u.display_name COLLATE NOCASE"""
        ).fetchall()
        latest_by_creator: dict[str, str | None] = {}
        if self._table_exists(con, "esp_backstage_evidence"):
            result["records"] = int(self._scalar(con, "SELECT COUNT(*) FROM esp_backstage_evidence"))
            rows = con.execute(
                """SELECT creator_user_id,MAX(COALESCE(NULLIF(captured_at,''),created_at)) latest
                   FROM esp_backstage_evidence GROUP BY creator_user_id"""
            ).fetchall()
            latest_by_creator = {row["creator_user_id"]: row["latest"] for row in rows}
            result["creators_with_evidence"] = len(latest_by_creator)
        for creator in creators:
            latest = latest_by_creator.get(creator["user_id"])
            state = _freshness(latest)
            result["freshness"][state] += 1
            if state in {"missing", "stale", "update_due"}:
                result["needs_update"].append({
                    "user_id": creator["user_id"],
                    "display_name": creator["display_name"],
                    "tiktok_handle": creator["tiktok_handle"] or "",
                    "region": creator["region"] or "",
                    "freshness": state,
                    "latest_evidence_at": latest,
                })
        order = {"missing": 0, "stale": 1, "update_due": 2}
        result["needs_update"].sort(key=lambda row: (order.get(row["freshness"], 9), row["display_name"].lower()))
        result["needs_update"] = result["needs_update"][:100]
        return result

    def _mentoring(self, con: sqlite3.Connection) -> dict:
        result = {
            "active_assignments": 0,
            "assigned_agents": 0,
            "assigned_creators": 0,
            "open_checkins": 0,
            "completed_checkins": 0,
            "active_success_plans": 0,
            "open_followups": 0,
        }
        if self._table_exists(con, "esp_agent_creator_assignments"):
            row = con.execute(
                """SELECT COUNT(*) n,COUNT(DISTINCT agent_user_id) agents,COUNT(DISTINCT creator_user_id) creators
                   FROM esp_agent_creator_assignments WHERE status='active'"""
            ).fetchone()
            if row:
                result["active_assignments"] = int(row["n"] or 0)
                result["assigned_agents"] = int(row["agents"] or 0)
                result["assigned_creators"] = int(row["creators"] or 0)
        if self._table_exists(con, "esp_agent_checkins"):
            rows = con.execute("SELECT status,COUNT(*) n FROM esp_agent_checkins GROUP BY status").fetchall()
            for row in rows:
                if row["status"] == "open":
                    result["open_checkins"] = int(row["n"] or 0)
                elif row["status"] == "completed":
                    result["completed_checkins"] = int(row["n"] or 0)
        if self._table_exists(con, "esp_creator_success_plans"):
            result["active_success_plans"] = int(self._scalar(
                con, "SELECT COUNT(*) FROM esp_creator_success_plans WHERE status='active'"
            ))
        if self._table_exists(con, "esp_agent_followups"):
            result["open_followups"] = int(self._scalar(
                con, "SELECT COUNT(*) FROM esp_agent_followups WHERE status='open'"
            ))
        return result

    def _recruitment(self, con: sqlite3.Connection) -> dict:
        result = {"leads": 0, "contact_allowed": 0, "do_not_contact": 0, "pipeline": {}}
        if not self._table_exists(con, "esp_creator_discovery_leads"):
            return result
        result["leads"] = int(self._scalar(con, "SELECT COUNT(*) FROM esp_creator_discovery_leads"))
        result["contact_allowed"] = int(self._scalar(
            con, "SELECT COUNT(*) FROM esp_creator_discovery_leads WHERE contact_allowed=1 AND do_not_contact=0"
        ))
        result["do_not_contact"] = int(self._scalar(
            con, "SELECT COUNT(*) FROM esp_creator_discovery_leads WHERE do_not_contact=1"
        ))
        rows = con.execute(
            "SELECT pipeline_status,COUNT(*) n FROM esp_creator_discovery_leads GROUP BY pipeline_status ORDER BY n DESC"
        ).fetchall()
        result["pipeline"] = {str(row["pipeline_status"]): int(row["n"] or 0) for row in rows}
        return result

    def _usage(self, con: sqlite3.Connection) -> dict:
        result = {"events": 0, "active_users": 0, "event_types": []}
        if not self._table_exists(con, "usage_events"):
            return result
        row = con.execute("SELECT COUNT(*) events,COUNT(DISTINCT user_id) users FROM usage_events").fetchone()
        if row:
            result["events"] = int(row["events"] or 0)
            result["active_users"] = int(row["users"] or 0)
        rows = con.execute(
            "SELECT event_type,COUNT(*) n FROM usage_events GROUP BY event_type ORDER BY n DESC,event_type LIMIT 25"
        ).fetchall()
        result["event_types"] = [{"event_type": row["event_type"], "count": int(row["n"] or 0)} for row in rows]
        return result

    def _support(self, con: sqlite3.Connection) -> dict:
        result = {"open": 0, "urgent_open": 0, "total": 0}
        if not self._table_exists(con, "esp_support_cases"):
            return result
        result["total"] = int(self._scalar(con, "SELECT COUNT(*) FROM esp_support_cases"))
        result["open"] = int(self._scalar(
            con, "SELECT COUNT(*) FROM esp_support_cases WHERE status NOT IN ('resolved','closed')"
        ))
        result["urgent_open"] = int(self._scalar(
            con, "SELECT COUNT(*) FROM esp_support_cases WHERE severity='urgent' AND status NOT IN ('resolved','closed')"
        ))
        return result

    def snapshot(self) -> dict:
        with self._connect() as con:
            roles = self._roles(con)
            return {
                "generated_at": _now().isoformat(),
                "roles": roles,
                "regions": self._regions(con),
                "training": self._training(con),
                "evidence": self._evidence(con),
                "mentoring": self._mentoring(con),
                "recruitment": self._recruitment(con),
                "usage": self._usage(con),
                "support": self._support(con),
                "privacy_boundary": "aggregate_operational_metadata_only",
                "private_creative_content_included": False,
                "esp_role_assignment_authority": "owner_only",
                "subscription_grants_esp_access": False,
            }


owner_esp_intelligence = OwnerEspNetworkIntelligenceStore()


@router.get("/owner/api/esp-intelligence")
def owner_esp_intelligence_api(request: Request):
    if not owner_session_authorized(request):
        raise HTTPException(403, "Owner access required")
    return owner_esp_intelligence.snapshot()


CSS = """
:root{--line:#ffffff1d;--muted:#c7bfd2;--good:#78dda5;--warn:#ffd17a;--bad:#ff91a5}*{box-sizing:border-box}body{margin:0;background:#07050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1320px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;font-weight:950}h1{font-size:clamp(2.5rem,6vw,5rem);line-height:.95;letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.card,.metric{border:1px solid var(--line);border-radius:17px;background:#15101deb;padding:15px;margin:10px 0}.metric b{display:block;font-size:1.5rem}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem;margin:2px}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;text-decoration:none;font-weight:850}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:left}.scroll{overflow:auto}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:500px){.grid{grid-template-columns:1fr}}
"""


def _metric(label: str, value, detail: str = "") -> str:
    return f"<div class='metric'><span class='muted'>{escape(label)}</span><b>{escape(str(value))}</b><small class='muted'>{escape(detail)}</small></div>"


@router.get("/owner/esp-intelligence", response_class=HTMLResponse, include_in_schema=False)
def owner_esp_intelligence_page(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = owner_esp_intelligence.snapshot()
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    roles = data["roles"]
    evidence = data["evidence"]
    mentoring = data["mentoring"]
    training = data["training"]
    recruitment = data["recruitment"]
    usage = data["usage"]
    support = data["support"]
    needs_rows = "".join(
        f"<tr><td>{escape(row['display_name'])}<br><small class='muted'>@{escape(row['tiktok_handle'])}</small></td>"
        f"<td>{escape(row['region'] or '—')}</td><td>{escape(row['freshness'].replace('_',' ').title())}</td>"
        f"<td>{escape(str(row['latest_evidence_at'] or 'No evidence'))}</td></tr>"
        for row in evidence["needs_update"][:30]
    ) or "<tr><td colspan='4' class='muted'>All currently tracked creator evidence is current.</td></tr>"
    pipeline = "".join(f"<span class='pill'>{escape(k.replace('_',' ').title())}: {v}</span>" for k, v in recruitment["pipeline"].items()) or "<span class='muted'>No recruitment leads recorded.</span>"
    event_types = "".join(f"<span class='pill'>{escape(row['event_type'].replace('_',' ').title())}: {row['count']}</span>" for row in usage["event_types"][:12]) or "<span class='muted'>No tracked creative usage yet.</span>"
    region_pills = "".join(f"<span class='pill'>{escape(row['region'])}: {row['members']}</span>" for row in data["regions"]) or "<span class='muted'>No regional ESP memberships.</span>"
    body = f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Network Intelligence</title><style>{CSS}:root{{--accent:{theme.accent};--secondary:{theme.secondary}}}body{{background:radial-gradient(circle at 90% 0,var(--secondary),transparent 30%),#07050c}}.eyebrow{{color:var(--accent)}}</style></head><body><main class='wrap'>
<div class='top'><div><div class='eyebrow'>Mary / Kev · Owner Intelligence</div><h1>ESP Network Intelligence</h1><p class='muted'>Owner-only operational analytics across authorised ESP Creator and Agent systems. This view does not inspect private creative project content and does not claim direct TikTok LIVE Backstage access.</p></div><div><a class='btn' href='/owner/dashboard'>Owner Dashboard</a><a class='btn' href='/owner/users'>Users</a></div></div>
<section class='grid'>{_metric('Active ESP',roles['active_esp'])}{_metric('Creators',roles['creators'])}{_metric('Agents',roles['agents'])}{_metric('Creator + Agent',roles['both'])}</section>
<section class='card'><div class='eyebrow'>Regions</div><h2>ESP membership distribution</h2>{region_pills}</section>
<section class='grid'>{_metric('Training learners',training['learners'],f"{training['average_percent']}% average")}{_metric('Evidence records',evidence['records'],f"{evidence['creators_with_evidence']} creators covered")}{_metric('Active assignments',mentoring['active_assignments'],f"{mentoring['assigned_agents']} agents")}{_metric('Open support',support['open'],f"{support['urgent_open']} urgent")}</section>
<section class='card'><div class='eyebrow'>Backstage / Manage Creator evidence</div><h2>Data freshness</h2><div class='grid'>{_metric('Current',evidence['freshness']['current'])}{_metric('Update due',evidence['freshness']['update_due'])}{_metric('Stale',evidence['freshness']['stale'])}{_metric('Missing',evidence['freshness']['missing'])}</div><p class='muted'>Direct TikTok LIVE Backstage connection: <b>No</b>. Freshness is calculated from uploaded/confirmed ESP evidence only.</p><div class='scroll'><table><thead><tr><th>Creator</th><th>Region</th><th>Status</th><th>Latest evidence</th></tr></thead><tbody>{needs_rows}</tbody></table></div></section>
<section class='card'><div class='eyebrow'>Mentoring operations</div><div class='grid'>{_metric('Assigned creators',mentoring['assigned_creators'])}{_metric('Open check-ins',mentoring['open_checkins'])}{_metric('Open follow-ups',mentoring['open_followups'])}{_metric('Active success plans',mentoring['active_success_plans'])}</div></section>
<section class='card'><div class='eyebrow'>Recruitment CRM</div><div class='grid'>{_metric('Leads',recruitment['leads'])}{_metric('Contact allowed',recruitment['contact_allowed'])}{_metric('Do not contact',recruitment['do_not_contact'])}{_metric('Usage events',usage['events'],f"{usage['active_users']} users")}</div><h3>Pipeline</h3>{pipeline}</section>
<section class='card'><div class='eyebrow'>Platform usage</div><h2>Feature activity metadata</h2>{event_types}<p class='muted'>Counts describe successful tracked actions. Raw private project prompts, files and creative content are excluded from this executive aggregate.</p></section>
</main></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


__all__ = ["router", "OwnerEspNetworkIntelligenceStore", "owner_esp_intelligence"]
