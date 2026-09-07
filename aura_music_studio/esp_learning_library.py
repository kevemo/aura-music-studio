from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Learning Library"])
ProgressStatus = Literal["not_started", "in_progress", "completed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Index only. Long-form source material remains in ESP-controlled knowledge storage and can
# be imported/updated by an owner workflow later without leaking confidential Drive folders.
RESOURCES: tuple[dict, ...] = (
    {"id":"creator-welcome","title":"Creator Welcome & Professional Standards","track":"Creator Foundations","audience":{"creator","owner"},"source_group":"Creator Companion / Welcome Pack","summary":"What ESP is, professional expectations, communication, conduct and how to use the private Hub.","action":"Confirm the standards and identify your first two creator goals."},
    {"id":"creator-viewers","title":"Get More Viewers","track":"Creator Academy","audience":{"creator","owner"},"source_group":"Creator Academy","summary":"Discovery, hooks, repeat viewers, LIVE entry experience and content-to-LIVE growth loops.","action":"Choose one discovery change to test in the next LIVE and record the result."},
    {"id":"creator-live-interface","title":"LIVE Stream Interface & Room Structure","track":"Creator Academy","audience":{"creator","owner"},"source_group":"Creator Academy","summary":"LIVE structure, room resets, engagement, show flow and audience participation.","action":"Write a repeatable opening and 15–20 minute room-reset loop."},
    {"id":"creator-music-host","title":"Music & Performing Arts LIVE Host Guide","track":"Niche Academy","audience":{"creator","owner"},"source_group":"Creator Academy / Musicians","summary":"Set planning, recurring performance segments, music discovery clips and fan-community development.","action":"Plan a structured music LIVE with at least three repeatable segments."},
    {"id":"creator-gaming-host","title":"Gaming LIVE Host Guide","track":"Niche Academy","audience":{"creator","owner"},"source_group":"Creator Academy / Gamers","summary":"Commentary, challenge loops, progression, highlights and gaming-community retention.","action":"Define one challenge loop and one clip-worthy recurring moment."},
    {"id":"creator-lifestyle-host","title":"Lifestyle LIVE Host Guide","track":"Niche Academy","audience":{"creator","owner"},"source_group":"Creator Academy / Lifestyle","summary":"Personality-led LIVE structure, daily-life storytelling, recurring segments and boundaries.","action":"Create three repeatable lifestyle content pillars and one LIVE segment for each."},
    {"id":"creator-health","title":"Creator Health Score Guide","track":"Creator Success","audience":{"creator","agent","owner"},"source_group":"Creator Academy","summary":"Balanced review of consistency, community, content quality, reliability, compliance, learning and improvement.","action":"Review current progress and choose one support priority rather than chasing every metric at once."},
    {"id":"creator-video-strategy","title":"Master Video Strategy","track":"Content","audience":{"creator","agent","owner"},"source_group":"Agent Academy / Creator Training","summary":"Hooks, searchable topics, pre-LIVE video, content pillars, testing and repeatable short-form systems.","action":"Plan the next five videos around one audience promise and different hooks."},
    {"id":"capcut-editing","title":"CapCut Master Editing Guide","track":"Content","audience":{"creator","agent","owner"},"source_group":"Agent Academy / Creator Training","summary":"Editing workflow, pacing, captions, cuts, effects and creator-focused short-form production.","action":"Edit one existing clip with tighter pacing and a clearer first-second hook."},
    {"id":"creator-incentives","title":"Creator Incentives & Eligibility","track":"Rewards","audience":{"creator","agent","owner"},"source_group":"ESP Incentives","summary":"Current ESP incentive, recognition, maintenance/rank, ambassador, equipment and competition pathways; eligibility remains programme-specific.","action":"Identify the incentive pathway you are currently closest to and verify its active eligibility rules."},
    {"id":"creator-campaigns","title":"Campaigns & Platform Opportunities","track":"Growth","audience":{"creator","agent","owner"},"source_group":"Creator Companion / Campaigns","summary":"Campaign readiness, participation, deadlines, deliverables and professional follow-through.","action":"Review active opportunities and confirm only those you can deliver professionally."},
    {"id":"creator-shop","title":"Commerce / Shop Readiness","track":"Commerce","audience":{"creator","agent","owner"},"source_group":"Creator Companion / Shop Creators","summary":"Shop education, product-fit, samples, deadlines, disclosures and creator/seller collaboration where available.","action":"Complete the current regional readiness checks before accepting a Shop opportunity."},
    {"id":"creator-wellbeing","title":"Creator Care, Boundaries & Sustainable Performance","track":"Wellbeing","audience":{"creator","agent","owner"},"source_group":"Creator Companion / Mental Health Matters","summary":"Non-clinical creator wellbeing, workload, confidence, boundaries, harassment escalation and professional signposting.","action":"Set one sustainable schedule boundary and identify the correct escalation route if support is needed."},
    {"id":"agent-01","title":"Module 1 — Apply Links, Recruitment & Agent Foundations","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Professional recruitment foundations, apply-link process, eligibility and ESP boundaries.","action":"Demonstrate the approved validation and recruitment workflow without poaching or duplicate contact."},
    {"id":"agent-02","title":"Module 2 — Gifting, ACU & Engagement","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Creator engagement metrics, gifting context and support-focused interpretation.","action":"Explain how engagement signals inform coaching without reducing creator value to gifts alone."},
    {"id":"agent-03","title":"Module 3 — LIVE Structure & Roadmap","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"How agents coach LIVE structure, consistency, retention and creator development roadmaps.","action":"Review one assigned creator and draft a concrete next-LIVE improvement plan."},
    {"id":"agent-04","title":"Module 4 — Creator Tiers, KPI & Diamond Systems","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"KPI interpretation, creator tiers and responsible performance coaching.","action":"Create a balanced creator scorecard that includes consistency and improvement, not diamonds alone."},
    {"id":"agent-05","title":"Module 5 — Master Video Strategy","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Video funnel coaching, hook testing, niche search intent and content review.","action":"Give one assigned creator three specific video tests for the next seven days."},
    {"id":"agent-06","title":"Module 6 — CapCut Master Editing Guide","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Editing knowledge agents can use when reviewing creator content and production quality.","action":"Review a creator clip and give precise edit feedback on hook, pacing and captions."},
    {"id":"agent-07","title":"Module 7 — Build Your Business","track":"Agent Apprentice","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Professional agency-building, responsibility, communication and sustainable team growth.","action":"Write the operating rhythm you will use for recruitment, creator support and follow-up."},
    {"id":"agent-08","title":"Campaigns, Tools & Platform Mastery","track":"Agent Advanced","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Campaigns, creator tools and platform workflows used in professional creator support.","action":"Map the tools you use to the creator problem each one solves; remove redundant busywork."},
    {"id":"agent-advanced","title":"Advanced Agent Training — Modules 41–50","track":"Agent Advanced","audience":{"agent","owner"},"source_group":"Agent Apprentice Program","summary":"Advanced operational, leadership and creator-success training for experienced agents.","action":"Complete the current advanced competency checkpoint before taking on expanded responsibilities."},
    {"id":"esp-operating-system","title":"ESP Global Creator Network Operating System","track":"ESP Standards","audience":{"creator","agent","owner"},"source_group":"ESP Operating System","summary":"Creator-first growth, ethical operations, mentoring, performance intelligence, collaboration and professional conduct.","action":"Identify which operating responsibility belongs to you in your current ESP role."},
    {"id":"owner-expansion","title":"ESP Expansion Blueprint","track":"Owner Strategy","audience":{"owner"},"source_group":"Owner / Competitive Expansion","summary":"Execution roadmap for Creator OS, specialist services, partnerships, commercial systems, experiences, governance and scale.","action":"Review capability status and assign an accountable owner to the next live service."},
    {"id":"owner-system-audit","title":"ESP Full System Audit & Control Gaps","track":"Owner Strategy","audience":{"owner"},"source_group":"Owner / Command Center","summary":"Owner visibility, priority, momentum, agent performance, recruitment funnel, compliance and speed-of-control requirements.","action":"Review the current Focus dashboard and assign the top three human actions for this cycle."},
)


class ProgressRequest(BaseModel):
    status: ProgressStatus
    evidence_note: str = Field(default="", max_length=2000)


def _role(membership: dict) -> str:
    if membership.get("status") == "owner":
        return "owner"
    value = str(membership.get("roles") or "").lower()
    return value if value in {"creator", "agent", "both"} else "creator"


def _can_view(resource: dict, role: str) -> bool:
    audience = set(resource.get("audience") or set())
    if role == "owner":
        return True
    if role == "both":
        return bool(audience & {"creator", "agent"})
    return role in audience


class LearningProgressStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self):
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_learning_progress (
                    user_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_started',
                    evidence_note TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,resource_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_learning_user_status
                    ON esp_learning_progress(user_id,status,updated_at DESC);
                """
            )

    def for_user(self, user_id: str) -> dict[str, dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM esp_learning_progress WHERE user_id=?", (user_id,)).fetchall()
        return {row["resource_id"]: dict(row) for row in rows}

    def set(self, user_id: str, resource_id: str, status: str, evidence_note: str = "") -> dict:
        if status not in {"not_started", "in_progress", "completed"}:
            raise ValueError("Unsupported learning status")
        now = _now()
        with self._connect() as con:
            old = con.execute(
                "SELECT * FROM esp_learning_progress WHERE user_id=? AND resource_id=?",
                (user_id, resource_id),
            ).fetchone()
            started_at = (old["started_at"] if old else None) or (now if status != "not_started" else None)
            completed_at = now if status == "completed" else None
            con.execute(
                """INSERT INTO esp_learning_progress
                   (user_id,resource_id,status,evidence_note,started_at,completed_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,resource_id) DO UPDATE SET
                     status=excluded.status,evidence_note=excluded.evidence_note,
                     started_at=COALESCE(esp_learning_progress.started_at,excluded.started_at),
                     completed_at=excluded.completed_at,updated_at=excluded.updated_at""",
                (user_id, resource_id, status, " ".join((evidence_note or "").split())[:2000], started_at, completed_at, now),
            )
            row = con.execute(
                "SELECT * FROM esp_learning_progress WHERE user_id=? AND resource_id=?",
                (user_id, resource_id),
            ).fetchone()
        return dict(row)


progress = LearningProgressStore()


def library_for(user_id: str, membership: dict, *, query: str = "", track: str = "") -> dict:
    role = _role(membership)
    states = progress.for_user(user_id)
    q = (query or "").strip().lower()
    t = (track or "").strip().lower()
    rows: list[dict] = []
    for resource in RESOURCES:
        if not _can_view(resource, role):
            continue
        searchable = " ".join(str(resource.get(k) or "") for k in ("title", "track", "summary", "source_group", "action")).lower()
        if q and q not in searchable:
            continue
        if t and t != str(resource["track"]).lower():
            continue
        row = {key: (sorted(value) if isinstance(value, set) else value) for key, value in resource.items()}
        state = states.get(resource["id"]) or {"status": "not_started", "evidence_note": "", "started_at": None, "completed_at": None}
        row["progress"] = {key: state.get(key) for key in ("status", "evidence_note", "started_at", "completed_at")}
        rows.append(row)
    completed = sum(1 for row in rows if row["progress"]["status"] == "completed")
    return {
        "role": role,
        "resources": rows,
        "tracks": sorted({row["track"] for row in rows}),
        "completed": completed,
        "total": len(rows),
        "completion_percent": round(completed / len(rows) * 100, 1) if rows else 0.0,
        "role_restricted": True,
        "long_form_source_exposed": False,
    }


@router.get("/command-center/api/library")
def library_api(request: Request, q: str = "", track: str = ""):
    member, membership = require_esp_hub_member(request)
    return library_for(member.user_id, membership, query=q, track=track)


@router.post("/command-center/api/library/{resource_id}/progress")
def update_learning_progress(resource_id: str, body: ProgressRequest, request: Request):
    member, membership = require_esp_hub_member(request)
    role = _role(membership)
    resource = next((row for row in RESOURCES if row["id"] == resource_id), None)
    if resource is None:
        raise HTTPException(404, "ESP learning resource not found")
    if not _can_view(resource, role):
        raise HTTPException(403, "This ESP learning resource is not available to your role")
    try:
        state = progress.set(member.user_id, resource_id, body.status, body.evidence_note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"resource_id": resource_id, "progress": state}


@router.get("/command-center/library", response_class=HTMLResponse, include_in_schema=False)
def library_page(request: Request):
    member, membership = require_esp_hub_member(request)
    data = library_for(member.user_id, membership)
    rows = "".join(
        f"<article class='card'><div class='top'><div><span class='pill'>{escape(str(row['track']))}</span><h3>{escape(str(row['title']))}</h3></div><span class='state'>{escape(str(row['progress']['status']).replace('_',' ').title())}</span></div><p>{escape(str(row['summary']))}</p><p class='action'><b>Action:</b> {escape(str(row['action']))}</p><div class='muted'>Source group: {escape(str(row['source_group']))}</div></article>"
        for row in data["resources"]
    ) or "<div class='card muted'>No resources are available for this role/filter.</div>"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Academy & Resource Library</title><style>
    :root{{--line:#ffffff1e;--gold:#f1c86f;--violet:#9f70ff;--muted:#c2bfd0;--good:#76dda6}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#45145f,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1200px,calc(100% - 28px));margin:auto;padding:38px 0 70px}}.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:9px;flex-wrap:wrap}}.eyebrow{{font-size:.7rem;color:var(--gold);letter-spacing:.15em;text-transform:uppercase;font-weight:950}}h1{{font-size:clamp(2.7rem,7vw,5.4rem);letter-spacing:-.06em;line-height:.93;margin:.13em 0}}p,.muted{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.card{{border:1px solid var(--line);border-radius:16px;padding:14px;background:#14101deb}}.card h3{{margin:8px 0 3px}}.pill,.state,.btn{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem;font-weight:850}}.state{{color:var(--good)}}.btn{{border-radius:9px;background:#ffffff08}}.action{{border-left:3px solid var(--violet);padding-left:9px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Role-Restricted Academy</div><h1>Academy & Resource Library</h1><p>Only resources authorised for your ESP role are indexed here. Completion: <b>{data['completed']}/{data['total']} ({data['completion_percent']}%)</b>.</p></div><div><a class='btn' href='/command-center/member-hub'>Member Hub</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><section class='grid'>{rows}</section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "RESOURCES", "LearningProgressStore", "library_for"]
