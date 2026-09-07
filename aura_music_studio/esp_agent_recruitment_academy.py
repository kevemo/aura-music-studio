from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Agent Recruitment Academy"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_agent(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership


CURRICULUM: tuple[dict, ...] = (
    {
        "id": "recruit-01",
        "title": "Ethical Recruitment & ESP Boundaries",
        "category": "Foundations",
        "summary": "Recruit adults professionally without poaching, pressure, deceptive claims or multi-account circumvention.",
        "methods": [
            "Explain ESP accurately and never present the network as TikTok itself.",
            "Do not recruit anyone known or indicated to be represented by another TikTok LIVE Creator Network.",
            "Public silence is not proof that a creator is unrepresented; final affiliation/eligibility requires authorised TikTok tooling or owner validation.",
            "Never encourage a creator to leave another network, hide affiliation or open another LIVE account.",
            "Use only legitimate public/authorised outreach channels and respect opt-outs.",
        ],
        "practice": "Explain to a prospect why ESP will not proceed until Creator Network eligibility is verified.",
    },
    {
        "id": "recruit-02",
        "title": "Ideal Creator Profile & Niche Fit",
        "category": "Discovery",
        "summary": "Find serious LIVE-host prospects by region, niche, professionalism and evidence of creator intent rather than follower count alone.",
        "methods": [
            "Start with ESP-supported region routing and adult eligibility.",
            "Look for consistent creator activity, communication ability, niche clarity and LIVE-host potential.",
            "Use niche-specific searches across music, gaming, lifestyle, education, beauty, food, fitness and other supported categories.",
            "Treat follower count as context, not a quality gate.",
            "Record the public source that supports each lead instead of inventing missing facts.",
        ],
        "practice": "Review three hypothetical profiles and choose which one deserves the first human validation step and why.",
    },
    {
        "id": "recruit-03",
        "title": "Creator Discovery System & Regional Handle Finder",
        "category": "Discovery",
        "summary": "Use the ESP Creator Discovery CRM efficiently while preserving dedupe, region and contact-safety controls.",
        "methods": [
            "Search public creator signals by authorised region, city/locality, niche and relevant public keywords/hashtags.",
            "Normalise TikTok handles before adding a lead and check the global ESP dedupe state.",
            "Record source URL/source type, region evidence and validation status.",
            "Keep do-not-contact, other-network and owner-validation states authoritative.",
            "Never convert a discovery result into an invitation until the appropriate validation gate is satisfied.",
        ],
        "practice": "Take a public handle from discovery through source recording, dedupe, qualification and validation-ready status.",
    },
    {
        "id": "recruit-04",
        "title": "Professional First Contact",
        "category": "Outreach",
        "summary": "Open a relevant conversation that explains value clearly without spam, earnings promises or pressure.",
        "methods": [
            "Personalise the opening to the creator's genuine public niche or work.",
            "State the opportunity accurately: creator support, training, mentoring and network services where eligible.",
            "Explain core eligibility plainly and invite questions.",
            "Use one clear next step rather than a wall of links or repeated messages.",
            "Record contact method/date and stop future outreach after an opt-out or conflicting network affiliation.",
        ],
        "practice": "Draft a concise first-contact approach for a musician, gamer and lifestyle creator without making unverifiable claims.",
    },
    {
        "id": "recruit-05",
        "title": "Recruitment Video Strategy",
        "category": "Content",
        "summary": "Use short-form educational recruitment content to generate inbound interest instead of relying only on direct outreach.",
        "methods": [
            "Lead with a creator problem, question or misconception rather than 'join my agency' as the only hook.",
            "Build separate videos for LIVE hosts, aspiring hosts and prospective independent agents.",
            "Use searchable wording naturally in on-screen text, speech, caption and topic selection.",
            "Rotate proof/education, opportunity explanation, FAQ, myth-busting, creator support and behind-the-scenes themes.",
            "Test hook, opening visual and CTA independently so learning is attributable.",
            "Never promise earnings, guaranteed growth, verification or acceptance.",
        ],
        "practice": "Create a five-video recruitment test plan using five different hooks for one target audience.",
    },
    {
        "id": "recruit-06",
        "title": "Recruitment LIVE Strategy",
        "category": "LIVE Recruitment",
        "summary": "Run professional recruitment LIVE sessions that educate, qualify and answer questions rather than pressure viewers.",
        "methods": [
            "Open with who the LIVE is for, what viewers will learn and the core eligibility boundary.",
            "Use repeatable segments: what a Creator Network is, what ESP support covers, eligibility, creator training, agent opportunity and Q&A.",
            "Reset the room regularly for new arrivals without repeating a hard sell.",
            "Use a clear opt-in CTA for people who want more information.",
            "Log serious inbound prospects in the CRM after the LIVE and follow the same validation rules as every other lead.",
        ],
        "practice": "Build a 60-minute recruitment LIVE run-of-show with recurring room resets and FAQ segments.",
    },
    {
        "id": "recruit-07",
        "title": "Qualification Conversation",
        "category": "Qualification",
        "summary": "Determine fit through professional questions about goals, LIVE intent, region, eligibility and expectations.",
        "methods": [
            "Ask what the creator wants to build and why LIVE matters to them.",
            "Confirm adult/region/account-good-standing requirements through the approved process rather than appearance assumptions.",
            "Ask whether they are currently represented by a TikTok LIVE Creator Network and stop if there is a conflict.",
            "Explain the one-LIVE-account expectation and professional communication requirements.",
            "Do not oversell; a poor fit should be recorded honestly rather than forced into onboarding.",
        ],
        "practice": "Conduct a mock qualification conversation and identify both fit signals and stop conditions.",
    },
    {
        "id": "recruit-08",
        "title": "Objections, Questions & Trust",
        "category": "Conversation",
        "summary": "Answer reasonable concerns with evidence and boundaries instead of manipulative closing tactics.",
        "methods": [
            "Clarify what ESP does and does not control.",
            "Explain that creator support and creative subscriptions are separate permission dimensions.",
            "If asked about earnings, never guarantee results; explain that creator performance varies.",
            "If someone needs time, provide the information needed and allow them to decide without repeated pressure.",
            "Escalate policy, legal, financial or unusual eligibility questions to ownership rather than guessing.",
        ],
        "practice": "Respond to five common objections using factual, non-pressuring language.",
    },
    {
        "id": "recruit-09",
        "title": "Follow-Up & CRM Discipline",
        "category": "Pipeline",
        "summary": "Keep recruitment organised without creating duplicate or unwanted contact.",
        "methods": [
            "Every contact or meaningful status change belongs in the lead record.",
            "Use follow-up tasks only where follow-up is appropriate and permitted.",
            "Do not contact opted-out, conflicting-network or do-not-contact leads.",
            "Keep next action, owner and validation state visible so agents do not duplicate one another's work.",
            "Measure response/conversion by source and method to improve the system rather than simply increasing volume.",
        ],
        "practice": "Clean a hypothetical ten-lead pipeline and identify which leads should be contacted, paused, escalated or closed.",
    },
    {
        "id": "recruit-10",
        "title": "From Interest to ESP Activation",
        "category": "Onboarding",
        "summary": "Move a qualified prospect through owner-controlled activation without bypassing approval boundaries.",
        "methods": [
            "Complete required eligibility and Creator Network validation steps before activation.",
            "Only Mary/Kev ownership can grant ESP Creator, Agent or Both roles.",
            "Do not treat a paid Pulsar subscription as ESP membership.",
            "After activation, route the new member to niche selection, standards, training and their assigned support pathway.",
            "Preserve an auditable record of the decision and role granted.",
        ],
        "practice": "Map the exact handoff from interested prospect to owner decision to activated ESP Creator dashboard.",
    },
    {
        "id": "recruit-11",
        "title": "Recruitment Analytics & Improvement",
        "category": "Analytics",
        "summary": "Use funnel data to improve quality, not to reward indiscriminate outreach volume.",
        "methods": [
            "Track discovery source → validated lead → contact → response → qualified → application → owner activation.",
            "Compare conversion by niche, region, source, content hook and recruitment method.",
            "Track duplicate/contact-safety exclusions separately so agents are not rewarded for bypassing controls.",
            "Review time-to-response and follow-up outcomes.",
            "Use the results to improve targeting, training and scripts while preserving professional standards.",
        ],
        "practice": "Review a sample funnel and recommend two quality improvements without weakening eligibility rules.",
    },
    {
        "id": "recruit-12",
        "title": "Mentor Mindset After Recruitment",
        "category": "Creator Success",
        "summary": "Recruitment is the beginning of creator support, not the finish line.",
        "methods": [
            "Create the first creator-success check-in quickly after activation.",
            "Review niche, goals, training and uploaded performance evidence before giving generic advice.",
            "Build agreed 7/30/60/90-day development milestones where appropriate.",
            "Document follow-ups and escalate support needs.",
            "Judge recruitment quality partly by creator development and retention, not sign-up count alone.",
        ],
        "practice": "Build a first-30-day support plan for a newly activated creator.",
    },
)

SCENARIOS: tuple[dict, ...] = (
    {
        "id": "scenario-affiliation",
        "title": "Possible Existing Network Affiliation",
        "prompt": "A promising creator says they 'might already be linked to an agency' but is unsure what kind.",
        "options": [
            "Tell them to leave immediately so ESP can invite them.",
            "Pause recruitment and route the affiliation question through the approved validation process.",
            "Ask them to use another LIVE account so there is no conflict.",
        ],
        "correct": 1,
        "explanation": "Potential Creator Network affiliation is a stop/validation condition. Do not poach or suggest account circumvention.",
    },
    {
        "id": "scenario-earnings",
        "title": "Earnings Question",
        "prompt": "A prospect asks, 'How much will I definitely make if I join?'",
        "options": [
            "Promise a minimum so they feel confident.",
            "Give the best creator's result as though it is typical.",
            "Explain the support available while making clear that earnings/performance are not guaranteed.",
        ],
        "correct": 2,
        "explanation": "Recruitment must not use guaranteed earnings or misleading performance claims.",
    },
    {
        "id": "scenario-optout",
        "title": "No Further Contact",
        "prompt": "A lead replies that they are not interested and asks not to be contacted again.",
        "options": [
            "Mark do-not-contact and stop future recruitment outreach.",
            "Wait two days and try a different agent.",
            "Move them to a new list so the previous contact is hidden.",
        ],
        "correct": 0,
        "explanation": "A clear opt-out is authoritative and must be respected across the recruitment system.",
    },
    {
        "id": "scenario-subscription",
        "title": "Paid Member Wants ESP Tools",
        "prompt": "A Pro subscriber assumes payment automatically gives them Agent access.",
        "options": [
            "Enable Agent tools because Pro is the top paid tier.",
            "Explain that Pulsar subscription and ESP role are separate and Mary/Kev must activate an ESP role.",
            "Give temporary Agent access until ownership reviews it.",
        ],
        "correct": 1,
        "explanation": "Paid creative plans never grant ESP Creator or Agent permissions.",
    },
)


class ProgressRequest(BaseModel):
    completed: bool = True
    evidence_note: str = Field(default="", max_length=2000)


class ScenarioAttempt(BaseModel):
    option_index: int = Field(ge=0, le=10)


class RecruitmentAcademyStore:
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
                CREATE TABLE IF NOT EXISTS esp_agent_recruitment_learning (
                    user_id TEXT NOT NULL,
                    module_id TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    evidence_note TEXT NOT NULL DEFAULT '',
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,module_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_agent_recruitment_attempts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    option_index INTEGER NOT NULL,
                    correct INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_recruitment_attempts_user
                    ON esp_agent_recruitment_attempts(user_id,created_at DESC);
                """
            )

    def states(self, user_id: str) -> dict[str, dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM esp_agent_recruitment_learning WHERE user_id=?", (user_id,)).fetchall()
        return {row["module_id"]: dict(row) for row in rows}

    def set_module(self, user_id: str, module_id: str, *, completed: bool, evidence_note: str = "") -> dict:
        if module_id not in {row["id"] for row in CURRICULUM}:
            raise KeyError(module_id)
        now = _now()
        note = " ".join((evidence_note or "").split())[:2000]
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_recruitment_learning
                   (user_id,module_id,completed,evidence_note,completed_at,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(user_id,module_id) DO UPDATE SET completed=excluded.completed,
                     evidence_note=excluded.evidence_note,completed_at=excluded.completed_at,updated_at=excluded.updated_at""",
                (user_id, module_id, int(completed), note, now if completed else None, now),
            )
            row = con.execute(
                "SELECT * FROM esp_agent_recruitment_learning WHERE user_id=? AND module_id=?",
                (user_id, module_id),
            ).fetchone()
        item = dict(row)
        item["completed"] = bool(item["completed"])
        return item

    def attempt(self, user_id: str, scenario_id: str, option_index: int) -> dict:
        scenario = next((row for row in SCENARIOS if row["id"] == scenario_id), None)
        if scenario is None:
            raise KeyError(scenario_id)
        if option_index < 0 or option_index >= len(scenario["options"]):
            raise ValueError("Choose a valid scenario response")
        correct = option_index == scenario["correct"]
        attempt_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_recruitment_attempts(id,user_id,scenario_id,option_index,correct,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (attempt_id, user_id, scenario_id, option_index, int(correct), _now()),
            )
        return {
            "attempt_id": attempt_id,
            "scenario_id": scenario_id,
            "correct": correct,
            "explanation": scenario["explanation"],
        }

    def summary(self, user_id: str) -> dict:
        states = self.states(user_id)
        completed = sum(1 for row in CURRICULUM if states.get(row["id"], {}).get("completed"))
        with self._connect() as con:
            attempts = con.execute(
                "SELECT COUNT(*) attempts,SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct FROM esp_agent_recruitment_attempts WHERE user_id=?",
                (user_id,),
            ).fetchone()
        attempt_count = int(attempts["attempts"] or 0) if attempts else 0
        correct_count = int(attempts["correct"] or 0) if attempts else 0
        return {
            "modules_completed": completed,
            "modules_total": len(CURRICULUM),
            "module_percent": round(completed / len(CURRICULUM) * 100, 1),
            "scenario_attempts": attempt_count,
            "scenario_correct": correct_count,
            "scenario_percent": round(correct_count / attempt_count * 100, 1) if attempt_count else 0.0,
            "certified": completed == len(CURRICULUM) and attempt_count >= len(SCENARIOS) and correct_count == attempt_count,
        }


academy = RecruitmentAcademyStore()


def academy_for(user_id: str) -> dict:
    states = academy.states(user_id)
    modules = []
    for source in CURRICULUM:
        row = dict(source)
        row["progress"] = states.get(source["id"]) or {
            "completed": False, "evidence_note": "", "completed_at": None
        }
        row["progress"]["completed"] = bool(row["progress"].get("completed"))
        modules.append(row)
    scenarios = [
        {key: value for key, value in row.items() if key not in {"correct", "explanation"}}
        for row in SCENARIOS
    ]
    return {
        "modules": modules,
        "scenarios": scenarios,
        "summary": academy.summary(user_id),
        "agent_only": True,
        "ethical_recruitment_required": True,
        "poaching_allowed": False,
        "earnings_guarantees_allowed": False,
        "owner_activation_required": True,
    }


@router.get("/command-center/api/agent/recruitment-academy")
def recruitment_academy_api(request: Request):
    member, _membership = _require_agent(request)
    return academy_for(member.user_id)


@router.put("/command-center/api/agent/recruitment-academy/modules/{module_id}")
def recruitment_module_progress_api(module_id: str, body: ProgressRequest, request: Request):
    member, _membership = _require_agent(request)
    try:
        state = academy.set_module(member.user_id, module_id, completed=body.completed, evidence_note=body.evidence_note)
    except KeyError as exc:
        raise HTTPException(404, "Recruitment Academy module not found") from exc
    return {"module_id": module_id, "progress": state, "summary": academy.summary(member.user_id)}


@router.post("/command-center/api/agent/recruitment-academy/scenarios/{scenario_id}")
def recruitment_scenario_api(scenario_id: str, body: ScenarioAttempt, request: Request):
    member, _membership = _require_agent(request)
    try:
        return academy.attempt(member.user_id, scenario_id, body.option_index)
    except KeyError as exc:
        raise HTTPException(404, "Recruitment scenario not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


CSS = """
:root{--line:#ffffff1f;--muted:#c7bfd1;--gold:#efc66b;--violet:#a16eff;--green:#78dda5}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#42185d,transparent 30%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-size:.72rem;text-transform:uppercase;letter-spacing:.14em;font-weight:950}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.25rem}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:850}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}ul{line-height:1.6}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""


@router.get("/command-center/agent/recruitment-academy", response_class=HTMLResponse, include_in_schema=False)
def recruitment_academy_page(request: Request):
    member, _membership = _require_agent(request)
    data = academy_for(member.user_id)
    summary = data["summary"]
    cards = "".join(
        "<article class='card'>"
        f"<div class='row'><div><span class='pill'>{escape(row['category'])}</span><h2>{escape(row['title'])}</h2></div>"
        f"<b>{'Completed' if row['progress']['completed'] else 'Learning'}</b></div>"
        f"<p class='muted'>{escape(row['summary'])}</p><ul>{''.join(f'<li>{escape(item)}</li>' for item in row['methods'])}</ul>"
        f"<p><b>Practice:</b> {escape(row['practice'])}</p></article>"
        for row in data["modules"]
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>"
        f"<title>ESP Recruitment Academy</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div>"
        "<div class='eyebrow'>Elevate Souls Productions · Agent Academy</div><h1>Recruitment Mastery</h1>"
        "<p class='muted'>Professional recruitment, discovery, content, LIVE strategy, qualification, follow-up and creator-success practice—inside ESP's non-poaching and owner-approval boundaries.</p></div>"
        "<div><a class='btn' href='/command-center/agent/discovery'>Creator Discovery</a><a class='btn primary' href='/command-center/level-up'>Agent OS</a></div></div>"
        f"<section class='grid'><div class='metric'><span class='muted'>Modules</span><b>{summary['modules_completed']}/{summary['modules_total']}</b></div>"
        f"<div class='metric'><span class='muted'>Training</span><b>{summary['module_percent']}%</b></div><div class='metric'><span class='muted'>Scenario score</span><b>{summary['scenario_percent']}%</b></div>"
        f"<div class='metric'><span class='muted'>Certified</span><b>{'Yes' if summary['certified'] else 'Not yet'}</b></div></section>{cards}</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "RecruitmentAcademyStore", "academy_for", "CURRICULUM", "SCENARIOS"]
