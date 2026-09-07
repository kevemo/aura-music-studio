from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_agent_roster import _require_agent
from .esp_command_center import esp

router = APIRouter(tags=["ESP Agent Creator Discovery"])

Affiliation = Literal["unknown", "none", "esp", "other_network", "unclear"]
AgeStatus = Literal["unknown", "adult_confirmed", "underage", "unclear"]
ValidationStatus = Literal["unreviewed", "validating", "eligible", "ineligible", "blocked"]
PipelineStatus = Literal[
    "new",
    "validating",
    "ready",
    "contacted",
    "follow_up_due",
    "replied",
    "applied",
    "joined",
    "not_interested",
    "closed",
]
SourceKind = Literal["manual", "public_profile", "creator_referral", "official_platform", "approved_dataset"]
EventType = Literal[
    "apply_message_sent",
    "follow_up_sent",
    "reply_received",
    "application_started",
    "joined",
    "not_interested",
    "opt_out",
    "note",
]

_HANDLE_RE = re.compile(r"^[a-z0-9._]{2,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_handle(value: str) -> str:
    handle = (value or "").strip().lower().lstrip("@").strip()
    if not _HANDLE_RE.fullmatch(handle):
        raise ValueError("Enter a valid public TikTok handle")
    return handle


class CreateLeadRequest(BaseModel):
    tiktok_handle: str = Field(min_length=2, max_length=80)
    display_name: str = Field(default="", max_length=160)
    region: str = Field(default="", max_length=120)
    niche: str = Field(default="", max_length=120)
    source_kind: SourceKind = "manual"
    source_ref: str = Field(min_length=1, max_length=1000)
    public_contact: str = Field(default="", max_length=320)
    notes: str = Field(default="", max_length=3000)


class ValidateLeadRequest(BaseModel):
    affiliation_status: Affiliation
    age_status: AgeStatus
    region: str | None = Field(default=None, max_length=120)
    niche: str | None = Field(default=None, max_length=120)
    eligibility_note: str = Field(default="", max_length=2000)


class EventRequest(BaseModel):
    event_type: EventType
    channel: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=2500)
    next_follow_up_at: str | None = Field(default=None, max_length=80)


class CreatorDiscoveryStore:
    """Agent recruitment CRM with global dedupe and fail-closed contact eligibility."""

    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
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
                CREATE TABLE IF NOT EXISTS esp_creator_discovery_leads (
                    id TEXT PRIMARY KEY,
                    tiktok_handle TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    niche TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    public_contact TEXT NOT NULL DEFAULT '',
                    assigned_agent_user_id TEXT,
                    affiliation_status TEXT NOT NULL DEFAULT 'unknown',
                    age_status TEXT NOT NULL DEFAULT 'unknown',
                    validation_status TEXT NOT NULL DEFAULT 'unreviewed',
                    pipeline_status TEXT NOT NULL DEFAULT 'new',
                    eligibility_note TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    contact_allowed INTEGER NOT NULL DEFAULT 0,
                    do_not_contact INTEGER NOT NULL DEFAULT 0,
                    last_contact_at TEXT,
                    next_follow_up_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(assigned_agent_user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_agent_pipeline
                    ON esp_creator_discovery_leads(assigned_agent_user_id,pipeline_status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_discovery_followup
                    ON esp_creator_discovery_leads(assigned_agent_user_id,next_follow_up_at);
                CREATE TABLE IF NOT EXISTS esp_creator_discovery_events (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    agent_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(lead_id) REFERENCES esp_creator_discovery_leads(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_events_lead
                    ON esp_creator_discovery_events(lead_id,created_at DESC);
                """
            )

    @staticmethod
    def _public(row: sqlite3.Row | dict) -> dict:
        item = dict(row)
        item["contact_allowed"] = bool(item.get("contact_allowed"))
        item["do_not_contact"] = bool(item.get("do_not_contact"))
        return item

    def create(self, agent_user_id: str, body: CreateLeadRequest) -> dict:
        handle = normalize_handle(body.tiktok_handle)
        now = _now()
        row = {
            "id": uuid4().hex,
            "tiktok_handle": handle,
            "display_name": " ".join(body.display_name.split())[:160],
            "region": " ".join(body.region.split())[:120],
            "niche": " ".join(body.niche.split())[:120],
            "source_kind": body.source_kind,
            "source_ref": body.source_ref.strip()[:1000],
            "public_contact": body.public_contact.strip()[:320],
            "assigned_agent_user_id": agent_user_id,
            "notes": " ".join(body.notes.split())[:3000],
            "created_at": now,
            "updated_at": now,
        }
        if not row["source_ref"]:
            raise ValueError("A public/approved source reference is required")
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO esp_creator_discovery_leads
                    (id,tiktok_handle,display_name,region,niche,source_kind,source_ref,public_contact,
                     assigned_agent_user_id,notes,created_at,updated_at)
                    VALUES (:id,:tiktok_handle,:display_name,:region,:niche,:source_kind,:source_ref,:public_contact,
                            :assigned_agent_user_id,:notes,:created_at,:updated_at)""",
                    row,
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileExistsError("This TikTok handle already exists in the ESP discovery system") from exc
            raise
        return self.get(row["id"], agent_user_id=agent_user_id)

    def get(self, lead_id: str, *, agent_user_id: str | None = None, owner: bool = False) -> dict:
        with self._connect() as con:
            if owner:
                row = con.execute("SELECT * FROM esp_creator_discovery_leads WHERE id=?", (lead_id,)).fetchone()
            else:
                row = con.execute(
                    "SELECT * FROM esp_creator_discovery_leads WHERE id=? AND assigned_agent_user_id=?",
                    (lead_id, agent_user_id),
                ).fetchone()
            if row is None:
                raise KeyError(lead_id)
            events = con.execute(
                "SELECT id,event_type,channel,note,created_at FROM esp_creator_discovery_events WHERE lead_id=? ORDER BY created_at DESC LIMIT 50",
                (lead_id,),
            ).fetchall()
        item = self._public(row)
        item["events"] = [dict(event) for event in events]
        return item

    def list(self, agent_user_id: str, *, owner: bool = False, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM esp_creator_discovery_leads"
        params: list[str] = []
        clauses: list[str] = []
        if not owner:
            clauses.append("assigned_agent_user_id=?")
            params.append(agent_user_id)
        if status:
            clauses.append("pipeline_status=?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY CASE pipeline_status WHEN 'follow_up_due' THEN 0 WHEN 'ready' THEN 1 WHEN 'validating' THEN 2 ELSE 3 END,updated_at DESC"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [self._public(row) for row in rows]

    @staticmethod
    def _validation_state(affiliation: str, age: str) -> tuple[str, str, bool, bool]:
        if affiliation in {"other_network", "esp"}:
            return "blocked", "closed", False, True
        if age == "underage":
            return "ineligible", "closed", False, True
        if affiliation == "none" and age == "adult_confirmed":
            return "eligible", "ready", True, False
        return "validating", "validating", False, False

    def validate(self, lead_id: str, agent_user_id: str, body: ValidateLeadRequest, *, owner: bool = False) -> dict:
        lead = self.get(lead_id, agent_user_id=agent_user_id, owner=owner)
        validation, pipeline, allowed, forced_dnc = self._validation_state(body.affiliation_status, body.age_status)
        dnc = bool(lead["do_not_contact"]) or forced_dnc
        if dnc:
            allowed = False
        fields = {
            "affiliation_status": body.affiliation_status,
            "age_status": body.age_status,
            "validation_status": validation,
            "pipeline_status": "closed" if dnc else pipeline,
            "contact_allowed": int(allowed),
            "do_not_contact": int(dnc),
            "eligibility_note": " ".join(body.eligibility_note.split())[:2000],
            "region": " ".join((body.region if body.region is not None else lead["region"]).split())[:120],
            "niche": " ".join((body.niche if body.niche is not None else lead["niche"]).split())[:120],
            "updated_at": _now(),
            "id": lead_id,
        }
        with self._connect() as con:
            con.execute(
                """UPDATE esp_creator_discovery_leads SET
                    affiliation_status=:affiliation_status,age_status=:age_status,validation_status=:validation_status,
                    pipeline_status=:pipeline_status,contact_allowed=:contact_allowed,do_not_contact=:do_not_contact,
                    eligibility_note=:eligibility_note,region=:region,niche=:niche,updated_at=:updated_at WHERE id=:id""",
                fields,
            )
        return self.get(lead_id, agent_user_id=agent_user_id, owner=owner)

    def add_event(self, lead_id: str, agent_user_id: str, body: EventRequest, *, owner: bool = False) -> dict:
        lead = self.get(lead_id, agent_user_id=agent_user_id, owner=owner)
        contact_events = {"apply_message_sent", "follow_up_sent"}
        if body.event_type in contact_events:
            if lead["do_not_contact"] or not lead["contact_allowed"]:
                raise PermissionError("This prospect is not eligible for recruitment contact")
        now = _now()
        event = {
            "id": uuid4().hex,
            "lead_id": lead_id,
            "agent_user_id": agent_user_id,
            "event_type": body.event_type,
            "channel": " ".join(body.channel.split())[:80],
            "note": " ".join(body.note.split())[:2500],
            "created_at": now,
        }
        pipeline = lead["pipeline_status"]
        contact_allowed = bool(lead["contact_allowed"])
        dnc = bool(lead["do_not_contact"])
        last_contact = lead.get("last_contact_at")
        next_due = (body.next_follow_up_at or "").strip()[:80] or lead.get("next_follow_up_at")
        if body.event_type == "apply_message_sent":
            pipeline, last_contact = "contacted", now
        elif body.event_type == "follow_up_sent":
            pipeline, last_contact = "contacted", now
        elif body.event_type == "reply_received":
            pipeline = "replied"
        elif body.event_type == "application_started":
            pipeline = "applied"
        elif body.event_type == "joined":
            pipeline, contact_allowed, next_due = "joined", False, None
        elif body.event_type == "not_interested":
            pipeline, contact_allowed, dnc, next_due = "not_interested", False, True, None
        elif body.event_type == "opt_out":
            pipeline, contact_allowed, dnc, next_due = "closed", False, True, None
        if next_due and pipeline == "contacted":
            pipeline = "follow_up_due"
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_creator_discovery_events
                   (id,lead_id,agent_user_id,event_type,channel,note,created_at)
                   VALUES (:id,:lead_id,:agent_user_id,:event_type,:channel,:note,:created_at)""",
                event,
            )
            con.execute(
                """UPDATE esp_creator_discovery_leads SET pipeline_status=?,contact_allowed=?,do_not_contact=?,
                   last_contact_at=?,next_follow_up_at=?,updated_at=? WHERE id=?""",
                (pipeline, int(contact_allowed), int(dnc), last_contact, next_due, now, lead_id),
            )
        return self.get(lead_id, agent_user_id=agent_user_id, owner=owner)

    def stats(self, agent_user_id: str, *, owner: bool = False) -> dict:
        rows = self.list(agent_user_id, owner=owner)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["pipeline_status"]] = counts.get(row["pipeline_status"], 0) + 1
        contacted = sum(counts.get(key, 0) for key in ("contacted", "follow_up_due", "replied", "applied", "joined"))
        joined = counts.get("joined", 0)
        return {
            "total": len(rows),
            "ready": counts.get("ready", 0),
            "follow_up_due": counts.get("follow_up_due", 0),
            "applied": counts.get("applied", 0),
            "joined": joined,
            "blocked_or_closed": counts.get("closed", 0),
            "contacted_or_beyond": contacted,
            "join_conversion_percent": round(joined / contacted * 100, 1) if contacted else 0.0,
            "counts": counts,
        }


discovery = CreatorDiscoveryStore()


def _context(request: Request):
    member, membership = _require_agent(request)
    owner = membership.get("status") == "owner"
    return member, membership, owner


@router.get("/command-center/api/agent/discovery")
def discovery_list(request: Request, status: str | None = None):
    member, _membership, owner = _context(request)
    return {
        "leads": discovery.list(member.user_id, owner=owner, status=status),
        "stats": discovery.stats(member.user_id, owner=owner),
        "global_handle_deduplication": True,
        "outreach_requires_validation": True,
        "other_network_outreach_blocked": True,
        "automatic_tiktok_dm": False,
    }


@router.post("/command-center/api/agent/discovery")
def discovery_create(body: CreateLeadRequest, request: Request):
    member, _membership, _owner = _context(request)
    try:
        return {"lead": discovery.create(member.user_id, body)}
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/agent/discovery/{lead_id}/validate")
def discovery_validate(lead_id: str, body: ValidateLeadRequest, request: Request):
    member, _membership, owner = _context(request)
    try:
        return {"lead": discovery.validate(lead_id, member.user_id, body, owner=owner)}
    except KeyError as exc:
        raise HTTPException(404, "Discovery prospect not found") from exc


@router.post("/command-center/api/agent/discovery/{lead_id}/events")
def discovery_event(lead_id: str, body: EventRequest, request: Request):
    member, _membership, owner = _context(request)
    try:
        return {"lead": discovery.add_event(lead_id, member.user_id, body, owner=owner)}
    except KeyError as exc:
        raise HTTPException(404, "Discovery prospect not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/command-center/api/agent/discovery/templates")
def discovery_templates(request: Request):
    _context(request)
    return {
        "apply_message": (
            "Hi @{handle}, I’m reaching out from Elevate Souls Productions. We support eligible TikTok LIVE creators with "
            "mentoring, training and creator-development resources. If you are 18+ and are not currently represented by "
            "another Creator Network, I can send you information about applying to join ESP. There is no obligation."
        ),
        "follow_up_1": (
            "Hi @{handle}, just following up on the ESP information I sent. If you would like to explore joining, I can "
            "share the official application route and answer any questions. If not, no problem and I won’t keep messaging you."
        ),
        "follow_up_2": (
            "Hi @{handle}, final follow-up from me about ESP. If you would like the application information, reply when convenient. "
            "Otherwise I’ll close the outreach so you receive no further recruitment follow-ups from me."
        ),
        "sending": "manual_or_official_provider_only",
        "bulk_tiktok_dm": False,
    }


CSS = r"""
:root{--line:#ffffff1d;--gold:#efc86f;--violet:#9f70ff;--muted:#c3bfd0;--good:#76dda6;--bad:#ff91a5;--warn:#ffc878}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#42155d,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}button,input,select{font:inherit}.wrap{width:min(1380px,calc(100% - 24px));margin:auto;padding:34px 0 70px}.top,.row{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}.eyebrow{font-size:.68rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.7rem,7vw,5.5rem);line-height:.93;letter-spacing:-.06em;margin:.13em 0}.muted{color:var(--muted);line-height:1.5}.btn,button{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160b1d}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.metric,.card{border:1px solid var(--line);border-radius:15px;padding:12px;background:#14101deb}.metric b{display:block;font-size:1.4rem}.form{display:grid;grid-template-columns:1fr 1fr 1fr;gap:7px}.field{width:100%;border:1px solid var(--line);border-radius:9px;background:#09070f;color:#fff;padding:9px}.table{width:100%;border-collapse:collapse;min-width:1000px}.table th,.table td{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}.table th{font-size:.65rem;color:#9d96a8;text-transform:uppercase}.scroll{overflow:auto}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 6px;font-size:.65rem}.good{color:var(--good)}.bad{color:var(--bad)}.notice{display:none}.notice.show{display:block;border:1px solid var(--line);border-radius:10px;padding:9px;margin:9px 0}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}.form{grid-template-columns:1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/agent/discovery',$=id=>document.getElementById(id);let leads=[];function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'#ff91a5':''}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}function stats(s){for(const [k,v] of Object.entries({total:s.total,ready:s.ready,follow:s.follow_up_due,applied:s.applied,joined:s.joined}))$(k).textContent=v??0;$('conversion').textContent=(s.join_conversion_percent??0)+'%'}function render(){const q=$('search').value.trim().toLowerCase();const rows=leads.filter(x=>!q||x.tiktok_handle.includes(q)||(x.display_name||'').toLowerCase().includes(q)||(x.region||'').toLowerCase().includes(q));$('rows').innerHTML=rows.length?rows.map(x=>`<tr><td><b>@${esc(x.tiktok_handle)}</b><br><span class="muted">${esc(x.display_name||'')}</span></td><td>${esc(x.region||'')}<br>${esc(x.niche||'')}</td><td><span class="pill">${esc(x.validation_status)}</span><br><span class="muted">network: ${esc(x.affiliation_status)} · age: ${esc(x.age_status)}</span></td><td><span class="pill ${x.contact_allowed?'good':x.do_not_contact?'bad':''}">${esc(x.pipeline_status)}</span><br>${x.next_follow_up_at?`<span class="muted">follow-up ${esc(x.next_follow_up_at)}</span>`:''}</td><td>${esc(x.source_kind)}<br><span class="muted">${esc(x.source_ref)}</span></td><td><button onclick="validateLead('${x.id}')">Validate</button> <button onclick="eventLead('${x.id}')">Log outreach</button></td></tr>`).join(''):'<tr><td colspan="6" class="muted">No matching prospects.</td></tr>'}async function load(){try{const d=await req(API);leads=d.leads||[];stats(d.stats||{});render()}catch(e){note(e.message,true)}}async function add(){const body={tiktok_handle:$('handle').value,display_name:$('name').value,region:$('region').value,niche:$('niche').value,source_kind:$('sourceKind').value,source_ref:$('sourceRef').value,public_contact:$('contact').value,notes:''};try{await req(API,{method:'POST',body:JSON.stringify(body)});['handle','name','region','niche','sourceRef','contact'].forEach(id=>$(id).value='');note('Prospect added for validation.');await load()}catch(e){note(e.message,true)}}async function validateLead(id){const affiliation=prompt('Affiliation: none / other_network / esp / unclear / unknown','unknown');if(!affiliation)return;const age=prompt('Age status: adult_confirmed / underage / unclear / unknown','unknown');if(!age)return;const eligibility_note=prompt('Validation note / evidence summary (no sensitive data):','')||'';try{await req(`${API}/${id}/validate`,{method:'PATCH',body:JSON.stringify({affiliation_status:affiliation,age_status:age,eligibility_note})});note('Validation updated.');await load()}catch(e){note(e.message,true)}}async function eventLead(id){const event_type=prompt('Event: apply_message_sent / follow_up_sent / reply_received / application_started / joined / not_interested / opt_out / note','apply_message_sent');if(!event_type)return;const channel=prompt('Channel used (e.g. TikTok DM, email, referral):','TikTok DM')||'';const message=prompt('Private outcome note:','')||'';const next_follow_up_at=prompt('Next follow-up date/time (optional):','')||null;try{await req(`${API}/${id}/events`,{method:'POST',body:JSON.stringify({event_type,channel,note:message,next_follow_up_at})});note('Outreach event logged.');await load()}catch(e){note(e.message,true)}}$('search').addEventListener('input',render);load();
"""


@router.get("/command-center/agent/discovery", response_class=HTMLResponse, include_in_schema=False)
def discovery_page(request: Request):
    _context(request)
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Creator Discovery</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div><h1>Creator Discovery</h1><p class='muted'>Validate legitimate public prospects, avoid duplicate contact, track apply messages and follow-ups, and measure applications/joins per agent. Creators represented by another Creator Network are blocked from recruitment outreach.</p></div><div><a class='btn' href='/command-center/member-hub'>Member Hub</a> <a class='btn' href='/command-center/agent/roster'>Assigned Roster</a></div></div><section class='grid'><div class='metric'><span class='muted'>Total</span><b id='total'>0</b></div><div class='metric'><span class='muted'>Ready</span><b id='ready'>0</b></div><div class='metric'><span class='muted'>Follow-up</span><b id='follow'>0</b></div><div class='metric'><span class='muted'>Applied / Joined</span><b><span id='applied'>0</span> / <span id='joined'>0</span></b></div><div class='metric'><span class='muted'>Join conversion</span><b id='conversion'>0%</b></div></section><div id='notice' class='notice'></div><section class='card'><div class='eyebrow'>Add public/approved prospect</div><div class='form'><input class='field' id='handle' placeholder='TikTok handle'><input class='field' id='name' placeholder='Display name'><input class='field' id='region' placeholder='Region'><input class='field' id='niche' placeholder='Niche'><select class='field' id='sourceKind'><option value='public_profile'>Public profile</option><option value='creator_referral'>Creator referral</option><option value='official_platform'>Official platform</option><option value='approved_dataset'>Approved dataset</option><option value='manual'>Manual</option></select><input class='field' id='sourceRef' placeholder='Public source URL/reference'><input class='field' id='contact' placeholder='Public contact route (optional)'><button class='primary' onclick='add()'>Add for validation</button></div></section><section class='card'><div class='top'><div class='eyebrow'>Per-agent recruitment CRM</div><input id='search' class='field' style='width:min(360px,100%)' placeholder='Search handle, name or region'></div><div class='scroll'><table class='table'><thead><tr><th>Creator</th><th>Region / niche</th><th>Validation</th><th>Pipeline</th><th>Source</th><th>Actions</th></tr></thead><tbody id='rows'><tr><td colspan='6' class='muted'>Loading…</td></tr></tbody></table></div></section><section class='card'><b>Compliance boundary</b><p class='muted'>No hidden-email harvesting, no uncontrolled bulk TikTok DMs, and no contact until age/network affiliation checks make the prospect eligible. Opt-outs and other-network affiliation disable recruitment contact.</p></section></main><script>{SCRIPT}</script></body></html>""", headers={"Cache-Control": "no-store"})


__all__ = ["router", "CreatorDiscoveryStore", "discovery", "normalize_handle"]
