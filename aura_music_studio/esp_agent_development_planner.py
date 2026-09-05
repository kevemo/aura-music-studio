from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_agent_roster import rosters
from .esp_backstage_evidence import backstage_evidence
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Agent Creator Development Planner"])

HORIZONS = (7, 30, 60, 90)
PlanStatus = Literal["active", "completed", "archived"]
MilestoneStatus = Literal["open", "done", "revised"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_after(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def _role(membership: dict) -> str:
    return "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()


def _require_agent_or_owner(request: Request):
    member, membership = require_esp_hub_member(request)
    role = _role(membership)
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership, role == "owner"


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


class StartDevelopmentPlanRequest(BaseModel):
    creator_user_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=3, max_length=1200)
    notes: str = Field(default="", max_length=3000)


class AddMilestoneRequest(BaseModel):
    horizon_days: int
    category: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=300)
    detail: str = Field(default="", max_length=2000)
    target_metric: str = Field(default="", max_length=80)
    target_value: float | None = None


class MilestoneUpdateRequest(BaseModel):
    status: MilestoneStatus
    evidence_note: str = Field(default="", max_length=2000)


class ReviewRequest(BaseModel):
    notes: str = Field(default="", max_length=3000)


class PlanStatusRequest(BaseModel):
    status: PlanStatus
    outcome: str = Field(default="", max_length=3000)


class AgentDevelopmentStore:
    """Human-led creator development plans for explicitly assigned ESP creators.

    The planner can summarise member-supplied evidence and suggest focus areas, but it never
    changes ESP roles, applies penalties, or claims direct TikTok LIVE Backstage access.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or rosters.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_agent_development_plans (
                    id TEXT PRIMARY KEY,
                    agent_user_id TEXT NOT NULL,
                    creator_user_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    outcome TEXT NOT NULL DEFAULT '',
                    baseline_metrics_json TEXT NOT NULL DEFAULT '{}',
                    baseline_evidence_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_development_one_active
                    ON esp_agent_development_plans(agent_user_id,creator_user_id)
                    WHERE status='active';
                CREATE INDEX IF NOT EXISTS idx_agent_development_creator
                    ON esp_agent_development_plans(creator_user_id,status,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_agent_development_milestones (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    target_metric TEXT NOT NULL DEFAULT '',
                    baseline_value REAL,
                    target_value REAL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    evidence_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(plan_id) REFERENCES esp_agent_development_plans(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_development_milestones
                    ON esp_agent_development_milestones(plan_id,horizon_days,status);

                CREATE TABLE IF NOT EXISTS esp_agent_development_reviews (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    reviewer_user_id TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    evidence_id TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES esp_agent_development_plans(id) ON DELETE CASCADE,
                    FOREIGN KEY(reviewer_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_development_reviews
                    ON esp_agent_development_reviews(plan_id,created_at DESC);
                """
            )

    def _active_creator(self, creator_user_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                """SELECT status,roles FROM esp_memberships WHERE user_id=?""",
                (creator_user_id,),
            ).fetchone()
        if not row or row["status"] not in {"active", "owner"}:
            return False
        return row["status"] == "owner" or (row["roles"] or "").lower() in {"creator", "both"}

    def _authorize_creator(self, actor_user_id: str, creator_user_id: str, *, owner: bool) -> None:
        if not self._active_creator(creator_user_id):
            raise ValueError("Creator does not have active ESP Creator access")
        if not owner and not rosters._active_assignment(actor_user_id, creator_user_id):
            raise PermissionError("Creator is not actively assigned to this agent")

    def _latest_evidence(self, actor_user_id: str, creator_user_id: str, *, owner: bool) -> dict | None:
        rows = backstage_evidence.list_for_creator(actor_user_id, creator_user_id, owner=owner, limit=1)
        return rows[0] if rows else None

    @staticmethod
    def _focus_suggestions(metrics: dict) -> list[dict]:
        def num(key: str):
            try:
                return float(metrics.get(key)) if metrics.get(key) is not None else None
            except (TypeError, ValueError):
                return None

        suggestions: list[dict] = []
        watch = num("avg_watch_seconds")
        if watch is not None and watch < 60:
            suggestions.append({
                "category": "Retention",
                "title": "Strengthen the opening minute and room-reset structure",
                "detail": "Review the first minute, recurring re-introductions and reasons to stay. Test one change at a time and compare the next evidence snapshot.",
                "target_metric": "avg_watch_seconds",
            })
        duration = num("duration_minutes")
        if duration is not None and duration < 120:
            suggestions.append({
                "category": "Consistency",
                "title": "Build toward a repeatable longer LIVE structure",
                "detail": "Use planned segments and pacing so the creator can sustain a quality session where practical rather than extending time without purpose.",
                "target_metric": "duration_minutes",
            })
        shares = num("shares")
        if shares is not None and shares <= 0:
            suggestions.append({
                "category": "Community",
                "title": "Create a clearly shareable LIVE moment",
                "detail": "Plan a useful, entertaining or emotionally resonant segment that naturally gives viewers a reason to share.",
                "target_metric": "shares",
            })
        follows = num("new_followers")
        if follows is not None and follows <= 0:
            suggestions.append({
                "category": "Conversion",
                "title": "Clarify the reason to return",
                "detail": "Tie the follow reason to recurring content, the next LIVE or a clear creator promise instead of repetitive generic calls to action.",
                "target_metric": "new_followers",
            })
        if not suggestions:
            suggestions.append({
                "category": "Growth",
                "title": "Protect what is working and test one measurable improvement",
                "detail": "Use the strongest current performance signal as the baseline and change only one major variable before the next review.",
                "target_metric": "",
            })
        return suggestions[:4]

    def start_plan(self, agent_user_id: str, creator_user_id: str, *, owner: bool, objective: str, notes: str = "") -> dict:
        self._authorize_creator(agent_user_id, creator_user_id, owner=owner)
        objective = _clean(objective, 1200)
        if not objective:
            raise ValueError("Development objective is required")
        evidence = self._latest_evidence(agent_user_id, creator_user_id, owner=owner)
        baseline = (evidence or {}).get("metrics") or {}
        now = _now()
        plan_id = uuid4().hex
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO esp_agent_development_plans
                       (id,agent_user_id,creator_user_id,objective,notes,status,outcome,baseline_metrics_json,
                        baseline_evidence_id,created_at,updated_at,completed_at)
                       VALUES (?,?,?,?,?,'active','',?,?,?,?,NULL)""",
                    (
                        plan_id, agent_user_id, creator_user_id, objective, _clean(notes, 3000),
                        json.dumps(baseline, sort_keys=True), (evidence or {}).get("id"), now, now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileExistsError("This creator already has an active development plan with this agent") from exc
            raise
        return self.get_plan(agent_user_id, plan_id, owner=owner)

    def add_milestone(
        self,
        agent_user_id: str,
        plan_id: str,
        *,
        owner: bool,
        horizon_days: int,
        category: str,
        title: str,
        detail: str = "",
        target_metric: str = "",
        target_value: float | None = None,
    ) -> dict:
        if horizon_days not in HORIZONS:
            raise ValueError("Milestone horizon must be 7, 30, 60 or 90 days")
        plan = self.get_plan(agent_user_id, plan_id, owner=owner)
        if plan["status"] != "active":
            raise ValueError("Milestones can only be added to an active plan")
        metric = _clean(target_metric, 80).lower().replace(" ", "_")
        baseline_value = None
        try:
            if metric and plan["baseline_metrics"].get(metric) is not None:
                baseline_value = float(plan["baseline_metrics"][metric])
        except (TypeError, ValueError):
            baseline_value = None
        now = _now()
        row_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_development_milestones
                   (id,plan_id,horizon_days,category,title,detail,target_metric,baseline_value,target_value,
                    due_at,status,evidence_note,created_at,updated_at,completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'open','',?,?,NULL)""",
                (
                    row_id, plan_id, horizon_days, _clean(category, 80), _clean(title, 300),
                    _clean(detail, 2000), metric, baseline_value, target_value, _date_after(horizon_days), now, now,
                ),
            )
        return self.get_plan(agent_user_id, plan_id, owner=owner)

    def set_milestone(
        self,
        agent_user_id: str,
        milestone_id: str,
        *,
        owner: bool,
        status: str,
        evidence_note: str = "",
    ) -> dict:
        if status not in {"open", "done", "revised"}:
            raise ValueError("Unsupported milestone status")
        with self._connect() as con:
            row = con.execute(
                """SELECT m.*,p.creator_user_id,p.agent_user_id FROM esp_agent_development_milestones m
                   JOIN esp_agent_development_plans p ON p.id=m.plan_id WHERE m.id=?""",
                (milestone_id,),
            ).fetchone()
        if not row:
            raise KeyError(milestone_id)
        if not owner and row["agent_user_id"] != agent_user_id:
            raise PermissionError("Milestone belongs to another agent")
        self._authorize_creator(agent_user_id, row["creator_user_id"], owner=owner)
        note = _clean(evidence_note, 2000)
        if status == "done" and not note:
            raise ValueError("Add a short evidence/review note before completing a milestone")
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_agent_development_milestones
                   SET status=?,evidence_note=?,updated_at=?,completed_at=? WHERE id=?""",
                (status, note, now, now if status == "done" else None, milestone_id),
            )
        return self.get_plan(agent_user_id, row["plan_id"], owner=owner)

    def add_review(self, agent_user_id: str, plan_id: str, *, owner: bool, notes: str = "") -> dict:
        plan = self.get_plan(agent_user_id, plan_id, owner=owner)
        evidence = self._latest_evidence(agent_user_id, plan["creator_user_id"], owner=owner)
        metrics = (evidence or {}).get("metrics") or {}
        review_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_development_reviews
                   (id,plan_id,reviewer_user_id,metrics_json,evidence_id,notes,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (review_id, plan_id, agent_user_id, json.dumps(metrics, sort_keys=True), (evidence or {}).get("id"), _clean(notes, 3000), _now()),
            )
        return self.get_plan(agent_user_id, plan_id, owner=owner)

    def set_plan_status(self, agent_user_id: str, plan_id: str, *, owner: bool, status: str, outcome: str = "") -> dict:
        if status not in {"active", "completed", "archived"}:
            raise ValueError("Unsupported development plan status")
        plan = self.get_plan(agent_user_id, plan_id, owner=owner)
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_agent_development_plans
                   SET status=?,outcome=?,updated_at=?,completed_at=? WHERE id=?""",
                (status, _clean(outcome, 3000), now, now if status == "completed" else None, plan_id),
            )
        return self.get_plan(agent_user_id, plan_id, owner=owner)

    def get_plan(self, agent_user_id: str, plan_id: str, *, owner: bool) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_agent_development_plans WHERE id=?", (plan_id,)).fetchone()
            if not row:
                raise KeyError(plan_id)
            if not owner and row["agent_user_id"] != agent_user_id:
                raise PermissionError("Development plan belongs to another agent")
            self._authorize_creator(agent_user_id, row["creator_user_id"], owner=owner)
            milestones = con.execute(
                "SELECT * FROM esp_agent_development_milestones WHERE plan_id=? ORDER BY horizon_days,created_at",
                (plan_id,),
            ).fetchall()
            reviews = con.execute(
                "SELECT * FROM esp_agent_development_reviews WHERE plan_id=? ORDER BY created_at DESC",
                (plan_id,),
            ).fetchall()
        item = dict(row)
        try:
            item["baseline_metrics"] = json.loads(item.pop("baseline_metrics_json") or "{}")
        except Exception:
            item["baseline_metrics"] = {}
        item["milestones"] = [dict(value) for value in milestones]
        decoded_reviews: list[dict] = []
        for value in reviews:
            review = dict(value)
            try:
                review["metrics"] = json.loads(review.pop("metrics_json") or "{}")
            except Exception:
                review["metrics"] = {}
            decoded_reviews.append(review)
        item["reviews"] = decoded_reviews
        total = len(item["milestones"])
        done = sum(1 for value in item["milestones"] if value["status"] == "done")
        item["completion"] = {"done": done, "total": total, "percent": round(done / total * 100, 1) if total else 0.0}
        latest_metrics = decoded_reviews[0]["metrics"] if decoded_reviews else item["baseline_metrics"]
        item["focus_suggestions"] = self._focus_suggestions(latest_metrics)
        item["direct_backstage_access"] = False
        item["automatic_penalties"] = False
        return item

    def plans_for_actor(self, actor_user_id: str, *, owner: bool) -> list[dict]:
        with self._connect() as con:
            if owner:
                rows = con.execute(
                    """SELECT p.id FROM esp_agent_development_plans p
                       JOIN esp_memberships m ON m.user_id=p.creator_user_id
                       WHERE m.status IN ('active','owner') ORDER BY p.updated_at DESC"""
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT p.id FROM esp_agent_development_plans p
                       JOIN esp_agent_creator_assignments a
                         ON a.agent_user_id=p.agent_user_id AND a.creator_user_id=p.creator_user_id
                       WHERE p.agent_user_id=? AND a.status='active' ORDER BY p.updated_at DESC""",
                    (actor_user_id,),
                ).fetchall()
        result: list[dict] = []
        for row in rows:
            try:
                result.append(self.get_plan(actor_user_id, row["id"], owner=owner))
            except (PermissionError, ValueError, KeyError):
                continue
        return result


development = AgentDevelopmentStore()


@router.get("/command-center/api/agent/development")
def development_dashboard_api(request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    return {
        "plans": development.plans_for_actor(member.user_id, owner=owner),
        "horizons": list(HORIZONS),
        "direct_backstage_access": False,
        "human_led": True,
        "automatic_penalties": False,
    }


@router.post("/command-center/api/agent/development/plans")
def start_development_plan_api(body: StartDevelopmentPlanRequest, request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    try:
        plan = development.start_plan(member.user_id, body.creator_user_id, owner=owner, objective=body.objective, notes=body.notes)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": plan}


@router.post("/command-center/api/agent/development/plans/{plan_id}/milestones")
def add_development_milestone_api(plan_id: str, body: AddMilestoneRequest, request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    try:
        plan = development.add_milestone(
            member.user_id, plan_id, owner=owner, horizon_days=body.horizon_days,
            category=body.category, title=body.title, detail=body.detail,
            target_metric=body.target_metric, target_value=body.target_value,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Development plan not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"plan": plan}


@router.patch("/command-center/api/agent/development/milestones/{milestone_id}")
def update_development_milestone_api(milestone_id: str, body: MilestoneUpdateRequest, request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    try:
        return {"plan": development.set_milestone(member.user_id, milestone_id, owner=owner, status=body.status, evidence_note=body.evidence_note)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Development milestone not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/agent/development/plans/{plan_id}/reviews")
def add_development_review_api(plan_id: str, body: ReviewRequest, request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    try:
        return {"plan": development.add_review(member.user_id, plan_id, owner=owner, notes=body.notes)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Development plan not found") from exc


@router.patch("/command-center/api/agent/development/plans/{plan_id}")
def update_development_plan_api(plan_id: str, body: PlanStatusRequest, request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    try:
        return {"plan": development.set_plan_status(member.user_id, plan_id, owner=owner, status=body.status, outcome=body.outcome)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Development plan not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


CSS = """
:root{--line:#ffffff20;--muted:#c8bfd2;--gold:#efc66b;--violet:#a26dff;--green:#78dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#42185d,transparent 30%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.5}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.25rem}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:800}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""


@router.get("/command-center/agent/development", response_class=HTMLResponse, include_in_schema=False)
def development_page(request: Request):
    member, _membership, owner = _require_agent_or_owner(request)
    plans = development.plans_for_actor(member.user_id, owner=owner)
    cards: list[str] = []
    for plan in plans:
        milestones = plan.get("milestones") or []
        groups = "".join(
            f"<div class='metric'><span class='muted'>{days}-day</span><b>{sum(1 for m in milestones if m['horizon_days']==days and m['status']=='done')}/{sum(1 for m in milestones if m['horizon_days']==days)}</b></div>"
            for days in HORIZONS
        )
        cards.append(
            "<article class='card'>"
            f"<div class='row'><div><span class='pill'>{escape(plan['status'].upper())}</span>"
            f"<h2>{escape(plan['objective'])}</h2><p class='muted'>Creator ID: {escape(plan['creator_user_id'])}</p></div>"
            f"<div><b>{plan['completion']['percent']}%</b><div class='muted'>milestones complete</div></div></div>"
            f"<div class='grid'>{groups}</div>"
            f"<p class='muted'>Baseline evidence is uploaded/authorised data only. Direct TikTok LIVE Backstage access: <b>No</b>.</p>"
            "</article>"
        )
    body = "".join(cards) or "<div class='card muted'>No active creator development plans yet. Start plans through the Agent Operations/API workflow after reviewing current creator evidence.</div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP Creator Development Planner</title><style>{CSS}</style></head>"
        f"<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div>"
        "<h1>Creator Development Planner</h1><p class='muted'>Human-led 7/30/60/90-day mentoring cycles built from uploaded creator evidence, training progress and agreed goals.</p></div>"
        "<div><a class='btn' href='/command-center/agent/backstage-evidence'>Backstage Evidence</a> <a class='btn primary' href='/command-center/agent/operations'>Agent Operations</a></div></div>"
        f"{body}</main></body></html>"
    )


__all__ = ["router", "AgentDevelopmentStore", "development", "HORIZONS"]
