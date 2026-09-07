from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import EspNicheStore, require_esp_hub_member

router = APIRouter(tags=["ESP Creator LIVE Show Planner"])
PlanStatus = Literal["draft", "ready", "completed", "archived"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _require_creator(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"creator", "both", "owner"}:
        raise HTTPException(403, "ESP Creator access is required for the LIVE Show Planner")
    return member


class ShowPlanCreate(BaseModel):
    title: str = Field(min_length=3, max_length=220)
    scheduled_at: str = Field(default="", max_length=80)
    target_duration_minutes: int = Field(default=120, ge=30, le=600)
    goal: str = Field(default="", max_length=1200)
    opening_hook: str = Field(default="", max_length=1200)
    primary_cta: str = Field(default="", max_length=800)
    room_reset_every_minutes: int = Field(default=20, ge=10, le=60)


class ShowPlanUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=220)
    scheduled_at: str | None = Field(default=None, max_length=80)
    target_duration_minutes: int | None = Field(default=None, ge=30, le=600)
    goal: str | None = Field(default=None, max_length=1200)
    opening_hook: str | None = Field(default=None, max_length=1200)
    primary_cta: str | None = Field(default=None, max_length=800)
    room_reset_every_minutes: int | None = Field(default=None, ge=10, le=60)


class SegmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    segment_type: str = Field(default="content", max_length=80)
    planned_minutes: int = Field(default=10, ge=1, le=180)
    script_notes: str = Field(default="", max_length=3000)
    audience_prompt: str = Field(default="", max_length=1200)
    cta: str = Field(default="", max_length=800)


class ChecklistUpdate(BaseModel):
    done: bool
    note: str = Field(default="", max_length=1200)


class StatusUpdate(BaseModel):
    status: PlanStatus
    review_notes: str = Field(default="", max_length=3000)


class CreatorLiveShowStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or esp.db_path
        self.niches = EspNicheStore(esp)
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
                CREATE TABLE IF NOT EXISTS esp_creator_live_show_plans (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL DEFAULT '',
                    target_duration_minutes INTEGER NOT NULL DEFAULT 120,
                    goal TEXT NOT NULL DEFAULT '',
                    opening_hook TEXT NOT NULL DEFAULT '',
                    primary_cta TEXT NOT NULL DEFAULT '',
                    room_reset_every_minutes INTEGER NOT NULL DEFAULT 20,
                    status TEXT NOT NULL DEFAULT 'draft',
                    review_notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_live_show_user
                    ON esp_creator_live_show_plans(user_id,status,updated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_creator_live_show_segments (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    segment_type TEXT NOT NULL DEFAULT 'content',
                    planned_minutes INTEGER NOT NULL,
                    script_notes TEXT NOT NULL DEFAULT '',
                    audience_prompt TEXT NOT NULL DEFAULT '',
                    cta TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES esp_creator_live_show_plans(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_live_show_segments
                    ON esp_creator_live_show_segments(plan_id,position);

                CREATE TABLE IF NOT EXISTS esp_creator_live_show_checklist (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    done INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES esp_creator_live_show_plans(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_live_show_checklist
                    ON esp_creator_live_show_checklist(plan_id,group_name,position);
                """
            )

    def _niche_title(self, user_id: str) -> str:
        profile = self.niches.get(user_id)
        return str(((profile or {}).get("catalog") or {}).get("title") or "your niche")

    @staticmethod
    def _default_durations(target: int) -> list[int]:
        weights = (0.05, 0.25, 0.15, 0.25, 0.20, 0.10)
        values = [max(1, int(round(target * weight))) for weight in weights]
        values[-1] += target - sum(values)
        return values

    def _default_segments(self, user_id: str, target: int) -> list[dict]:
        niche = self._niche_title(user_id)
        durations = self._default_durations(target)
        rows = (
            ("Opening hook & welcome", "opening", "State what is happening now, who the LIVE is for and why a viewer should stay.", "Ask one easy opening question that is natural for the room.", "Invite viewers to stay for the next named segment."),
            (f"Core {niche} segment A", "content", "Deliver the first main value, performance, story, demonstration or entertainment block.", "Use an audience question or choice that fits the niche.", "Tie the interaction back to the show promise."),
            ("Community interaction", "engagement", "Read the room, welcome people, respond to relevant comments and reset context for new arrivals.", "Use one clear participation prompt without spamming viewers.", "Explain what is coming next."),
            (f"Core {niche} segment B", "content", "Deliver the second main show block with a different angle, challenge, song, topic or demonstration.", "Invite useful questions or reactions around this segment.", "Connect the segment to the recurring creator promise."),
            ("Feature / recurring show moment", "feature", "Run the repeatable moment viewers can recognise and return for in future LIVEs.", "Use a participation mechanic appropriate to the creator's niche and community.", "Give viewers a reason to return for the next LIVE."),
            ("Close & next-LIVE promise", "close", "Recap the strongest moments, thank the room and clearly state what happens next.", "Ask for final questions or one closing community response.", "Use the creator's chosen follow/return CTA without pressure."),
        )
        now = _now()
        return [
            {
                "id": uuid4().hex,
                "position": index,
                "name": row[0],
                "segment_type": row[1],
                "planned_minutes": durations[index - 1],
                "script_notes": row[2],
                "audience_prompt": row[3],
                "cta": row[4],
                "created_at": now,
                "updated_at": now,
            }
            for index, row in enumerate(rows, 1)
        ]

    @staticmethod
    def _default_checklist() -> list[tuple[str, str, bool]]:
        return [
            ("Pre-LIVE", "Publish or prepare the pre-LIVE promotion/content touchpoint.", True),
            ("Pre-LIVE", "Confirm show topic, title and the first-minute hook.", True),
            ("Equipment", "Test microphone/audio level and remove avoidable background noise.", True),
            ("Equipment", "Check camera framing, lighting, power and stable internet connection.", True),
            ("Professional", "Prepare water, notes/cue cards and a tidy, appropriate host environment.", True),
            ("Professional", "Confirm the host will be present and the planned show follows current ESP/platform standards.", True),
            ("Post-LIVE", "After the LIVE, record one thing to keep and one thing to improve.", False),
        ]

    def create(self, user_id: str, body: ShowPlanCreate) -> dict:
        plan_id, now = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_creator_live_show_plans
                   (id,user_id,title,scheduled_at,target_duration_minutes,goal,opening_hook,primary_cta,
                    room_reset_every_minutes,status,review_notes,created_at,updated_at,completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'draft','',?,?,NULL)""",
                (
                    plan_id, user_id, _clean(body.title, 220), _clean(body.scheduled_at, 80),
                    body.target_duration_minutes, _clean(body.goal, 1200), _clean(body.opening_hook, 1200),
                    _clean(body.primary_cta, 800), body.room_reset_every_minutes, now, now,
                ),
            )
            for segment in self._default_segments(user_id, body.target_duration_minutes):
                con.execute(
                    """INSERT INTO esp_creator_live_show_segments
                       (id,plan_id,position,name,segment_type,planned_minutes,script_notes,audience_prompt,cta,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        segment["id"], plan_id, segment["position"], segment["name"], segment["segment_type"],
                        segment["planned_minutes"], segment["script_notes"], segment["audience_prompt"], segment["cta"], now, now,
                    ),
                )
            for position, (group_name, label, required) in enumerate(self._default_checklist(), 1):
                con.execute(
                    """INSERT INTO esp_creator_live_show_checklist
                       (id,plan_id,group_name,label,required,done,note,position,updated_at)
                       VALUES (?,?,?,?,?,0,'',?,?)""",
                    (uuid4().hex, plan_id, group_name, label, int(required), position, now),
                )
        return self.get(user_id, plan_id)

    def _row(self, user_id: str, plan_id: str):
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_live_show_plans WHERE id=? AND user_id=?",
                (plan_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError(plan_id)
        return row

    def get(self, user_id: str, plan_id: str) -> dict:
        row = self._row(user_id, plan_id)
        with self._connect() as con:
            segments = con.execute(
                "SELECT * FROM esp_creator_live_show_segments WHERE plan_id=? ORDER BY position,created_at",
                (plan_id,),
            ).fetchall()
            checklist = con.execute(
                "SELECT * FROM esp_creator_live_show_checklist WHERE plan_id=? ORDER BY position",
                (plan_id,),
            ).fetchall()
        item = dict(row)
        item["segments"] = [dict(segment) for segment in segments]
        item["checklist"] = [dict(check) | {"required": bool(check["required"]), "done": bool(check["done"])} for check in checklist]
        elapsed = 0
        timeline = []
        for segment in item["segments"]:
            start = elapsed
            elapsed += int(segment["planned_minutes"] or 0)
            timeline.append({
                "segment_id": segment["id"],
                "name": segment["name"],
                "start_minute": start,
                "end_minute": elapsed,
                "planned_minutes": int(segment["planned_minutes"] or 0),
            })
        item["timeline"] = timeline
        item["planned_total_minutes"] = elapsed
        required = [check for check in item["checklist"] if check["required"]]
        required_done = sum(1 for check in required if check["done"])
        item["readiness"] = {
            "required_done": required_done,
            "required_total": len(required),
            "required_percent": round(required_done / len(required) * 100, 1) if required else 100.0,
            "has_opening_hook": bool(item["opening_hook"].strip()),
            "has_minimum_segments": len(item["segments"]) >= 3,
            "ready_to_mark_ready": bool(item["opening_hook"].strip()) and len(item["segments"]) >= 3 and required_done == len(required),
        }
        item["room_reset_minutes"] = list(range(item["room_reset_every_minutes"], max(elapsed, 1), item["room_reset_every_minutes"]))
        item["direct_tiktok_scheduling"] = False
        item["automatic_live_control"] = False
        return item

    def list_for_user(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM esp_creator_live_show_plans WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",
                (user_id,),
            ).fetchall()
        return [self.get(user_id, row["id"]) for row in rows]

    def update(self, user_id: str, plan_id: str, body: ShowPlanUpdate) -> dict:
        current = dict(self._row(user_id, plan_id))
        if current["status"] in {"completed", "archived"}:
            raise ValueError("Completed or archived show plans must be reopened as a new plan")
        values = {
            "title": current["title"] if body.title is None else _clean(body.title, 220),
            "scheduled_at": current["scheduled_at"] if body.scheduled_at is None else _clean(body.scheduled_at, 80),
            "target_duration_minutes": current["target_duration_minutes"] if body.target_duration_minutes is None else body.target_duration_minutes,
            "goal": current["goal"] if body.goal is None else _clean(body.goal, 1200),
            "opening_hook": current["opening_hook"] if body.opening_hook is None else _clean(body.opening_hook, 1200),
            "primary_cta": current["primary_cta"] if body.primary_cta is None else _clean(body.primary_cta, 800),
            "room_reset_every_minutes": current["room_reset_every_minutes"] if body.room_reset_every_minutes is None else body.room_reset_every_minutes,
        }
        with self._connect() as con:
            con.execute(
                """UPDATE esp_creator_live_show_plans SET title=?,scheduled_at=?,target_duration_minutes=?,goal=?,
                   opening_hook=?,primary_cta=?,room_reset_every_minutes=?,updated_at=? WHERE id=? AND user_id=?""",
                (
                    values["title"], values["scheduled_at"], values["target_duration_minutes"], values["goal"],
                    values["opening_hook"], values["primary_cta"], values["room_reset_every_minutes"], _now(), plan_id, user_id,
                ),
            )
        return self.get(user_id, plan_id)

    def add_segment(self, user_id: str, plan_id: str, body: SegmentCreate) -> dict:
        plan = self.get(user_id, plan_id)
        if plan["status"] in {"completed", "archived"}:
            raise ValueError("Cannot add segments to a completed or archived show plan")
        position = max([int(row["position"]) for row in plan["segments"]], default=0) + 1
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_creator_live_show_segments
                   (id,plan_id,position,name,segment_type,planned_minutes,script_notes,audience_prompt,cta,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid4().hex, plan_id, position, _clean(body.name, 180), _clean(body.segment_type, 80) or "content",
                    body.planned_minutes, _clean(body.script_notes, 3000), _clean(body.audience_prompt, 1200),
                    _clean(body.cta, 800), now, now,
                ),
            )
            con.execute("UPDATE esp_creator_live_show_plans SET status='draft',updated_at=? WHERE id=?", (now, plan_id))
        return self.get(user_id, plan_id)

    def set_checklist(self, user_id: str, plan_id: str, item_id: str, body: ChecklistUpdate) -> dict:
        self._row(user_id, plan_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM esp_creator_live_show_checklist WHERE id=? AND plan_id=?",
                (item_id, plan_id),
            ).fetchone()
            if not row:
                raise KeyError(item_id)
            con.execute(
                "UPDATE esp_creator_live_show_checklist SET done=?,note=?,updated_at=? WHERE id=? AND plan_id=?",
                (int(body.done), _clean(body.note, 1200), _now(), item_id, plan_id),
            )
            con.execute("UPDATE esp_creator_live_show_plans SET updated_at=? WHERE id=?", (_now(), plan_id))
        return self.get(user_id, plan_id)

    def set_status(self, user_id: str, plan_id: str, body: StatusUpdate) -> dict:
        plan = self.get(user_id, plan_id)
        if body.status == "ready" and not plan["readiness"]["ready_to_mark_ready"]:
            raise ValueError("Complete the required readiness checks and opening hook before marking this show ready")
        if body.status == "completed" and plan["status"] not in {"ready", "draft"}:
            raise ValueError("Only an active show plan can be completed")
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_creator_live_show_plans SET status=?,review_notes=?,updated_at=?,completed_at=?
                   WHERE id=? AND user_id=?""",
                (
                    body.status, _clean(body.review_notes, 3000), now,
                    now if body.status == "completed" else None, plan_id, user_id,
                ),
            )
        return self.get(user_id, plan_id)


show_plans = CreatorLiveShowStore()


@router.get("/command-center/api/show-planner")
def show_planner_api(request: Request):
    member = _require_creator(request)
    return {
        "plans": show_plans.list_for_user(member.user_id),
        "direct_tiktok_scheduling": False,
        "automatic_live_control": False,
    }


@router.post("/command-center/api/show-planner/plans")
def create_show_plan_api(body: ShowPlanCreate, request: Request):
    member = _require_creator(request)
    return {"plan": show_plans.create(member.user_id, body)}


@router.get("/command-center/api/show-planner/plans/{plan_id}")
def get_show_plan_api(plan_id: str, request: Request):
    member = _require_creator(request)
    try:
        return {"plan": show_plans.get(member.user_id, plan_id)}
    except KeyError as exc:
        raise HTTPException(404, "LIVE show plan not found") from exc


@router.patch("/command-center/api/show-planner/plans/{plan_id}")
def update_show_plan_api(plan_id: str, body: ShowPlanUpdate, request: Request):
    member = _require_creator(request)
    try:
        return {"plan": show_plans.update(member.user_id, plan_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "LIVE show plan not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/show-planner/plans/{plan_id}/segments")
def add_show_segment_api(plan_id: str, body: SegmentCreate, request: Request):
    member = _require_creator(request)
    try:
        return {"plan": show_plans.add_segment(member.user_id, plan_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "LIVE show plan not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/show-planner/plans/{plan_id}/checklist/{item_id}")
def update_show_checklist_api(plan_id: str, item_id: str, body: ChecklistUpdate, request: Request):
    member = _require_creator(request)
    try:
        return {"plan": show_plans.set_checklist(member.user_id, plan_id, item_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "LIVE show plan/checklist item not found") from exc


@router.patch("/command-center/api/show-planner/plans/{plan_id}/status")
def update_show_status_api(plan_id: str, body: StatusUpdate, request: Request):
    member = _require_creator(request)
    try:
        return {"plan": show_plans.set_status(member.user_id, plan_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "LIVE show plan not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


CSS = """
:root{--line:#ffffff20;--muted:#c8bfd2;--gold:#efc66b;--violet:#a26dff;--green:#78dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42185d,transparent 31%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.5}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.25rem}.btn,button{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:800;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem}input,textarea{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#080610;color:#fff;margin:5px 0}textarea{min-height:80px}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = """
async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
async function makePlan(){const title=(document.getElementById('title').value||'').trim();if(!title)return alert('Add a show title.');const hook=(document.getElementById('hook').value||'').trim();try{const d=await api('/command-center/api/show-planner/plans',{method:'POST',body:JSON.stringify({title,opening_hook:hook,target_duration_minutes:120,room_reset_every_minutes:20})});location.href='/command-center/show-planner/'+encodeURIComponent(d.plan.id)}catch(e){alert(e.message)}}
async function toggle(plan,item,done){try{await api(`/command-center/api/show-planner/plans/${encodeURIComponent(plan)}/checklist/${encodeURIComponent(item)}`,{method:'PATCH',body:JSON.stringify({done:done,note:''})});location.reload()}catch(e){alert(e.message)}}
async function ready(plan){try{await api(`/command-center/api/show-planner/plans/${encodeURIComponent(plan)}/status`,{method:'PATCH',body:JSON.stringify({status:'ready',review_notes:''})});location.reload()}catch(e){alert(e.message)}}
"""


@router.get("/command-center/show-planner", response_class=HTMLResponse, include_in_schema=False)
def show_planner_page(request: Request):
    member = _require_creator(request)
    plans = show_plans.list_for_user(member.user_id)
    cards = "".join(
        "<article class='card'><div class='row'><div>"
        f"<span class='pill'>{escape(plan['status'].upper())}</span><h2>{escape(plan['title'])}</h2>"
        f"<p class='muted'>{plan['planned_total_minutes']} planned minutes · room resets every {plan['room_reset_every_minutes']} minutes · readiness {plan['readiness']['required_percent']}%</p>"
        "</div>"
        f"<a class='btn primary' href='/command-center/show-planner/{escape(plan['id'], quote=True)}'>Open show plan</a></div></article>"
        for plan in plans
    ) or "<div class='card muted'>No LIVE show plans yet. Build your first one below.</div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP LIVE Show Planner</title><style>{CSS}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Creator OS</div>"
        "<h1>LIVE Show Planner</h1><p class='muted'>Turn training into a timed, repeatable LIVE show structure with human-confirmed readiness checks.</p></div>"
        "<a class='btn' href='/command-center/dashboard'>Creator Dashboard</a></div>"
        f"{cards}<section class='card'><h2>Create a 120-minute show</h2><label>Show title</label><input id='title' placeholder='Friday Night Music & Requests'>"
        "<label>Opening hook</label><textarea id='hook' placeholder='Tell viewers immediately what is happening and why they should stay.'></textarea>"
        "<button class='primary' onclick='makePlan()'>Build show structure</button></section>"
        "<section class='card'><b>Platform boundary</b><p class='muted'>This planner prepares your show. It does not schedule, start, control or inspect a TikTok LIVE session directly.</p></section>"
        f"<script>{SCRIPT}</script></main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/command-center/show-planner/{plan_id}", response_class=HTMLResponse, include_in_schema=False)
def show_planner_detail_page(plan_id: str, request: Request):
    member = _require_creator(request)
    try:
        plan = show_plans.get(member.user_id, plan_id)
    except KeyError as exc:
        raise HTTPException(404, "LIVE show plan not found") from exc
    timeline = "".join(
        f"<article class='card'><div class='row'><div><span class='pill'>{segment['start_minute']}–{segment['end_minute']} min</span>"
        f"<h3>{escape(segment['name'])}</h3></div><b>{segment['planned_minutes']} min</b></div>"
        f"<p class='muted'>{escape(next((row['script_notes'] for row in plan['segments'] if row['id']==segment['segment_id']), ''))}</p></article>"
        for segment in plan["timeline"]
    )
    checklist = "".join(
        f"<article class='card'><div class='row'><div><span class='pill'>{escape(item['group_name'])}</span>"
        f"<p>{escape(item['label'])}</p></div><button onclick=\"toggle('{escape(plan_id, quote=True)}','{escape(item['id'], quote=True)}',{str(not item['done']).lower()})\">{'Reopen' if item['done'] else 'Complete'}</button></div></article>"
        for item in plan["checklist"]
    )
    reset_text = ", ".join(str(value) for value in plan["room_reset_minutes"]) or "None within current duration"
    ready_button = "<button class='primary' onclick=\"ready('" + escape(plan_id, quote=True) + "')\">Mark show ready</button>" if plan["status"] == "draft" else ""
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP LIVE Show Plan</title><style>{CSS}</style></head>"
        f"<body><main class='wrap'><div class='top'><div><div class='eyebrow'>ESP LIVE Show Plan</div><h1>{escape(plan['title'])}</h1>"
        f"<p class='muted'>Status: {escape(plan['status'].title())} · target {plan['target_duration_minutes']} min · planned {plan['planned_total_minutes']} min</p></div>"
        "<a class='btn' href='/command-center/show-planner'>All show plans</a></div>"
        f"<section class='grid'><div class='metric'><span class='muted'>Readiness</span><b>{plan['readiness']['required_percent']}%</b></div>"
        f"<div class='metric'><span class='muted'>Room reset cadence</span><b>{plan['room_reset_every_minutes']} min</b></div>"
        f"<div class='metric'><span class='muted'>Segments</span><b>{len(plan['segments'])}</b></div></section>"
        f"<section class='card'><b>Opening hook</b><p>{escape(plan['opening_hook'] or 'Add an opening hook before marking ready.')}</p>"
        f"<p class='muted'>Suggested room-reset minutes: {escape(reset_text)}</p>{ready_button}</section>"
        f"<h2>Timed show structure</h2>{timeline}<h2>Readiness checklist</h2>{checklist}"
        "<section class='card'><b>Human-led control</b><p class='muted'>Readiness is based on your saved plan and checks. Pulsar-Frequency House does not directly start, stop or inspect TikTok LIVE.</p></section>"
        f"<script>{SCRIPT}</script></main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "CreatorLiveShowStore", "show_plans"]
