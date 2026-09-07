from __future__ import annotations

import sqlite3
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .owner_identity import owner_session_authorized, owner_theme, request_owner_persona

router = APIRouter(tags=["ESP Owner Operations Intelligence"])


class OwnerEspOperationsIntelligenceStore:
    """Owner-only aggregates for newly built ESP mentoring, training and Shop systems.

    This intentionally reports counts/status metadata rather than private creator content,
    OAuth tokens, Shop payloads, evidence screenshots or internal creative project files.
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or AccountStore().db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _exists(con: sqlite3.Connection, table: str) -> bool:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    @staticmethod
    def _count(con: sqlite3.Connection, sql: str, params=()) -> int:
        try:
            row = con.execute(sql, params).fetchone()
            return int((row[0] if row else 0) or 0)
        except sqlite3.OperationalError:
            return 0

    def development(self, con: sqlite3.Connection) -> dict:
        result = {
            "plans": 0, "active_plans": 0, "completed_plans": 0,
            "milestones": 0, "open_milestones": 0, "completed_milestones": 0,
            "reviews": 0, "creators_planned": 0, "agents_planning": 0,
            "automatic_penalties": False,
        }
        if self._exists(con, "esp_agent_development_plans"):
            row = con.execute(
                """SELECT COUNT(*) plans,
                          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active_plans,
                          SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed_plans,
                          COUNT(DISTINCT creator_user_id) creators,
                          COUNT(DISTINCT agent_user_id) agents
                   FROM esp_agent_development_plans"""
            ).fetchone()
            result.update({
                "plans": int(row["plans"] or 0),
                "active_plans": int(row["active_plans"] or 0),
                "completed_plans": int(row["completed_plans"] or 0),
                "creators_planned": int(row["creators"] or 0),
                "agents_planning": int(row["agents"] or 0),
            })
        if self._exists(con, "esp_agent_development_milestones"):
            row = con.execute(
                """SELECT COUNT(*) milestones,
                          SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) open_milestones,
                          SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) completed_milestones
                   FROM esp_agent_development_milestones"""
            ).fetchone()
            result.update({
                "milestones": int(row["milestones"] or 0),
                "open_milestones": int(row["open_milestones"] or 0),
                "completed_milestones": int(row["completed_milestones"] or 0),
            })
        if self._exists(con, "esp_agent_development_reviews"):
            result["reviews"] = self._count(con, "SELECT COUNT(*) FROM esp_agent_development_reviews")
        return result

    def recruitment_academy(self, con: sqlite3.Connection) -> dict:
        result = {
            "agents_started": 0, "module_completions": 0, "scenario_attempts": 0,
            "scenario_correct": 0, "scenario_accuracy_percent": 0.0,
            "certification_decisions_automatic": False,
        }
        learners: set[str] = set()
        if self._exists(con, "esp_agent_recruitment_learning"):
            rows = con.execute(
                """SELECT user_id,
                          SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) complete
                   FROM esp_agent_recruitment_learning GROUP BY user_id"""
            ).fetchall()
            learners.update(str(row["user_id"]) for row in rows)
            result["module_completions"] = sum(int(row["complete"] or 0) for row in rows)
        if self._exists(con, "esp_agent_recruitment_attempts"):
            row = con.execute(
                """SELECT COUNT(*) attempts,
                          SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct,
                          COUNT(DISTINCT user_id) users
                   FROM esp_agent_recruitment_attempts"""
            ).fetchone()
            attempts = int(row["attempts"] or 0) if row else 0
            correct = int(row["correct"] or 0) if row else 0
            result["scenario_attempts"] = attempts
            result["scenario_correct"] = correct
            result["scenario_accuracy_percent"] = round(correct / attempts * 100, 1) if attempts else 0.0
            if row:
                # Include agents who used scenarios before ticking a module complete.
                scenario_users = con.execute(
                    "SELECT DISTINCT user_id FROM esp_agent_recruitment_attempts"
                ).fetchall()
                learners.update(str(value["user_id"]) for value in scenario_users)
        result["agents_started"] = len(learners)
        return result

    def shop(self, con: sqlite3.Connection) -> dict:
        result = {
            "connections": 0,
            "connection_states": {},
            "workflows": 0,
            "active_workflows": 0,
            "queued_actions": 0,
            "awaiting_approval": 0,
            "approved_not_executed": 0,
            "executed_actions": 0,
            "creators_using_shop_automation": 0,
            "provider_execution_claim_requires_reference": True,
            "raw_oauth_tokens_in_owner_aggregate": False,
        }
        users: set[str] = set()
        if self._exists(con, "esp_shop_connections"):
            rows = con.execute(
                "SELECT status,COUNT(*) n FROM esp_shop_connections GROUP BY status ORDER BY n DESC"
            ).fetchall()
            result["connection_states"] = {str(row["status"]): int(row["n"] or 0) for row in rows}
            result["connections"] = sum(result["connection_states"].values())
            user_rows = con.execute("SELECT DISTINCT user_id FROM esp_shop_connections").fetchall()
            users.update(str(row["user_id"]) for row in user_rows)
        if self._exists(con, "esp_shop_workflows"):
            row = con.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active
                   FROM esp_shop_workflows"""
            ).fetchone()
            if row:
                result["workflows"] = int(row["total"] or 0)
                result["active_workflows"] = int(row["active"] or 0)
            user_rows = con.execute("SELECT DISTINCT user_id FROM esp_shop_workflows").fetchall()
            users.update(str(row["user_id"]) for row in user_rows)
        if self._exists(con, "esp_shop_action_queue"):
            row = con.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='awaiting_approval' THEN 1 ELSE 0 END) awaiting,
                          SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) approved,
                          SUM(CASE WHEN status='executed' AND provider_execution_ref<>'' THEN 1 ELSE 0 END) executed
                   FROM esp_shop_action_queue"""
            ).fetchone()
            if row:
                result["queued_actions"] = int(row["total"] or 0)
                result["awaiting_approval"] = int(row["awaiting"] or 0)
                result["approved_not_executed"] = int(row["approved"] or 0)
                result["executed_actions"] = int(row["executed"] or 0)
            user_rows = con.execute("SELECT DISTINCT user_id FROM esp_shop_action_queue").fetchall()
            users.update(str(row["user_id"]) for row in user_rows)
        result["creators_using_shop_automation"] = len(users)
        return result

    def dashboard_views(self, con: sqlite3.Connection) -> dict:
        result = {"creator_view": 0, "agent_view": 0, "preferences": 0}
        if not self._exists(con, "esp_dashboard_preferences"):
            return result
        rows = con.execute("SELECT mode,COUNT(*) n FROM esp_dashboard_preferences GROUP BY mode").fetchall()
        for row in rows:
            value = int(row["n"] or 0)
            result["preferences"] += value
            if row["mode"] == "creator":
                result["creator_view"] = value
            elif row["mode"] == "agent":
                result["agent_view"] = value
        return result

    def snapshot(self) -> dict:
        with self._connect() as con:
            return {
                "development": self.development(con),
                "recruitment_academy": self.recruitment_academy(con),
                "shop": self.shop(con),
                "dashboard_views": self.dashboard_views(con),
                "privacy_boundary": "aggregate_operational_metadata_only",
                "private_creator_content_included": False,
                "raw_backstage_evidence_included": False,
                "raw_oauth_tokens_included": False,
                "subscription_grants_esp_access": False,
                "esp_role_assignment_authority": "owner_only",
            }


owner_esp_operations = OwnerEspOperationsIntelligenceStore()


@router.get("/owner/api/esp-operations-intelligence")
def owner_esp_operations_api(request: Request):
    if not owner_session_authorized(request):
        raise HTTPException(403, "Owner access required")
    return owner_esp_operations.snapshot()


CSS = """
:root{--line:#ffffff1f;--muted:#c8bfd2;--gold:#efc66b}*{box-sizing:border-box}body{margin:0;background:#07050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1220px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;font-weight:950;color:var(--accent,var(--gold))}h1{font-size:clamp(2.4rem,6vw,4.8rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.card,.metric{border:1px solid var(--line);border-radius:17px;background:#15101deb;padding:15px;margin:10px 0}.metric b{display:block;font-size:1.45rem}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;text-decoration:none;font-weight:850}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;margin:2px;font-size:.74rem}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""


def _metric(label: str, value, detail: str = "") -> str:
    return f"<div class='metric'><span class='muted'>{escape(label)}</span><b>{escape(str(value))}</b><small class='muted'>{escape(detail)}</small></div>"


@router.get("/owner/esp-operations-intelligence", response_class=HTMLResponse, include_in_schema=False)
def owner_esp_operations_page(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = owner_esp_operations.snapshot()
    persona = request_owner_persona(request)
    theme = owner_theme(persona)
    development = data["development"]
    academy = data["recruitment_academy"]
    shop = data["shop"]
    views = data["dashboard_views"]
    states = "".join(
        f"<span class='pill'>{escape(key.replace('_',' ').title())}: {value}</span>"
        for key, value in shop["connection_states"].items()
    ) or "<span class='muted'>No Shop connections recorded.</span>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>"
        f"<title>ESP Operations Intelligence</title><style>{CSS}:root{{--accent:{theme.accent};--secondary:{theme.secondary}}}body{{background:radial-gradient(circle at 90% 0,var(--secondary),transparent 30%),#07050c}}</style></head><body><main class='wrap'>"
        "<div class='top'><div><div class='eyebrow'>Mary / Kev · Owner Operations</div><h1>ESP Operations Intelligence</h1>"
        "<p class='muted'>Aggregate owner analytics for creator development, Agent recruitment training, Shop automation and role-dashboard usage. Private creative content, raw screenshots and OAuth tokens are not included.</p></div>"
        "<div><a class='btn' href='/owner/esp-intelligence'>Network Intelligence</a><a class='btn' href='/owner/dashboard'>Owner Dashboard</a></div></div>"
        f"<section class='card'><div class='eyebrow'>Creator development</div><div class='grid'>{_metric('Active plans',development['active_plans'])}{_metric('Creators planned',development['creators_planned'])}{_metric('Open milestones',development['open_milestones'])}{_metric('Reviews',development['reviews'])}</div></section>"
        f"<section class='card'><div class='eyebrow'>Agent Recruitment Academy</div><div class='grid'>{_metric('Agents started',academy['agents_started'])}{_metric('Module completions',academy['module_completions'])}{_metric('Scenario attempts',academy['scenario_attempts'])}{_metric('Scenario accuracy',str(academy['scenario_accuracy_percent'])+'%')}</div></section>"
        f"<section class='card'><div class='eyebrow'>Shop Creator automation</div><div class='grid'>{_metric('Creators using',shop['creators_using_shop_automation'])}{_metric('Active workflows',shop['active_workflows'])}{_metric('Awaiting approval',shop['awaiting_approval'])}{_metric('Verified executed',shop['executed_actions'])}</div><p>{states}</p><p class='muted'>Only actions with a provider execution reference count as externally executed. Raw OAuth tokens are never part of this aggregate.</p></section>"
        f"<section class='card'><div class='eyebrow'>Creator / Agent view</div><div class='grid'>{_metric('Saved preferences',views['preferences'])}{_metric('Creator view',views['creator_view'])}{_metric('Agent view',views['agent_view'])}{_metric('ESP access by subscription','Never')}</div></section>"
        "</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "OwnerEspOperationsIntelligenceStore", "owner_esp_operations"]
