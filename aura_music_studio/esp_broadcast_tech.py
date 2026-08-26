from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Pro Broadcast and Tech Desk"])

SetupType = Literal["mobile", "live_studio", "obs", "hybrid", "other"]
ChecklistState = Literal["not_started", "checked", "needs_help"]

CHECKLIST = [
    ("network_path", "Network path checked", "Confirm the connection is stable enough for the planned LIVE and avoid unnecessary competing traffic."),
    ("mic_input", "Microphone input verified", "Confirm the intended microphone is selected, audible and not clipping."),
    ("monitoring", "Headphone / monitoring path verified", "Confirm you can monitor the show without creating feedback or echo."),
    ("program_audio", "Music / programme audio routing verified", "Confirm backing tracks, game audio or other programme audio reach the LIVE mix at the intended level."),
    ("camera_frame", "Camera framing checked", "Check framing, focus, orientation and anything visible in the background."),
    ("lighting", "Lighting checked", "Check face/key lighting and avoid strong backlighting where possible."),
    ("scene_layout", "Scene / source layout checked", "Confirm overlays, capture sources and scene changes show only what you intend to broadcast."),
    ("privacy", "Privacy check completed", "Remove private notifications, personal files, addresses, credentials and other sensitive information from the broadcast view."),
    ("test_recording", "Local test recording completed", "Record a short local test and review audio/video sync, levels and image quality before a major LIVE."),
    ("backup_plan", "Backup plan ready", "Know how you will simplify or safely end the LIVE if audio, video or network quality fails."),
]

_SECRET_PATTERNS = [
    re.compile(r"(?i)password\s*[:=]"),
    re.compile(r"(?i)stream[ _-]?key\s*[:=]"),
    re.compile(r"(?i)(oauth|access|refresh)[ _-]?token\s*[:=]"),
    re.compile(r"(?i)(api|secret)[ _-]?key\s*[:=]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject_secrets(value: str) -> str:
    clean = (value or "").strip()
    if any(pattern.search(clean) for pattern in _SECRET_PATTERNS):
        raise ValueError("Do not store passwords, stream keys, OAuth tokens or secret/API keys in the ESP Tech Desk")
    return clean


class BroadcastProfileRequest(BaseModel):
    setup_type: SetupType = "other"
    operating_system: str = Field(default="", max_length=120)
    internet_type: str = Field(default="", max_length=120)
    microphone: str = Field(default="", max_length=240)
    audio_interface: str = Field(default="", max_length=240)
    camera: str = Field(default="", max_length=240)
    capture_card: str = Field(default="", max_length=240)
    notes: str = Field(default="", max_length=3000)
    consent_store_setup: bool = False


class ChecklistRequest(BaseModel):
    state: ChecklistState
    note: str = Field(default="", max_length=1000)


class NetworkTestRequest(BaseModel):
    download_mbps: float | None = Field(default=None, ge=0, le=100000)
    upload_mbps: float | None = Field(default=None, ge=0, le=100000)
    latency_ms: float | None = Field(default=None, ge=0, le=100000)
    jitter_ms: float | None = Field(default=None, ge=0, le=100000)
    packet_loss_percent: float | None = Field(default=None, ge=0, le=100)
    connection_note: str = Field(default="", max_length=1000)


class BroadcastTechStore:
    """Consent-based broadcast setup records; no credentials or remote-control secrets."""

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
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
                CREATE TABLE IF NOT EXISTS esp_broadcast_profiles (
                    user_id TEXT PRIMARY KEY,
                    setup_type TEXT NOT NULL DEFAULT 'other',
                    operating_system TEXT NOT NULL DEFAULT '',
                    internet_type TEXT NOT NULL DEFAULT '',
                    microphone TEXT NOT NULL DEFAULT '',
                    audio_interface TEXT NOT NULL DEFAULT '',
                    camera TEXT NOT NULL DEFAULT '',
                    capture_card TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    consent_store_setup INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_broadcast_checklist (
                    user_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'not_started',
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,item_key),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_network_tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    download_mbps REAL,
                    upload_mbps REAL,
                    latency_ms REAL,
                    jitter_ms REAL,
                    packet_loss_percent REAL,
                    connection_note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'member_reported',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_network_tests_member
                    ON esp_network_tests(user_id,created_at DESC);
                """
            )

    def profile(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_broadcast_profiles WHERE user_id=?", (user_id,)).fetchone()
        if row:
            item = dict(row)
            item["consent_store_setup"] = bool(item["consent_store_setup"])
            return item
        return {
            "user_id": user_id,
            "setup_type": "other",
            "operating_system": "",
            "internet_type": "",
            "microphone": "",
            "audio_interface": "",
            "camera": "",
            "capture_card": "",
            "notes": "",
            "consent_store_setup": False,
            "updated_at": None,
        }

    def save_profile(self, user_id: str, body: BroadcastProfileRequest) -> dict:
        values = {
            "setup_type": body.setup_type,
            "operating_system": _reject_secrets(body.operating_system)[:120],
            "internet_type": _reject_secrets(body.internet_type)[:120],
            "microphone": _reject_secrets(body.microphone)[:240],
            "audio_interface": _reject_secrets(body.audio_interface)[:240],
            "camera": _reject_secrets(body.camera)[:240],
            "capture_card": _reject_secrets(body.capture_card)[:240],
            "notes": _reject_secrets(body.notes)[:3000],
        }
        # Without consent, keep only the explicit consent flag and discard setup metadata.
        if not body.consent_store_setup:
            values = {key: "" for key in values} | {"setup_type": "other"}
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_broadcast_profiles
                   (user_id,setup_type,operating_system,internet_type,microphone,audio_interface,camera,capture_card,notes,consent_store_setup,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET setup_type=excluded.setup_type,operating_system=excluded.operating_system,
                     internet_type=excluded.internet_type,microphone=excluded.microphone,audio_interface=excluded.audio_interface,
                     camera=excluded.camera,capture_card=excluded.capture_card,notes=excluded.notes,
                     consent_store_setup=excluded.consent_store_setup,updated_at=excluded.updated_at""",
                (
                    user_id,
                    values["setup_type"],
                    values["operating_system"],
                    values["internet_type"],
                    values["microphone"],
                    values["audio_interface"],
                    values["camera"],
                    values["capture_card"],
                    values["notes"],
                    int(body.consent_store_setup),
                    _now(),
                ),
            )
        return self.profile(user_id)

    def checklist(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = {
                row["item_key"]: dict(row)
                for row in con.execute("SELECT * FROM esp_broadcast_checklist WHERE user_id=?", (user_id,)).fetchall()
            }
        return [
            {
                "key": key,
                "title": title,
                "guidance": guidance,
                "state": rows.get(key, {}).get("state", "not_started"),
                "note": rows.get(key, {}).get("note", ""),
                "updated_at": rows.get(key, {}).get("updated_at"),
            }
            for key, title, guidance in CHECKLIST
        ]

    def set_checklist(self, user_id: str, item_key: str, *, state: str, note: str = "") -> dict:
        if item_key not in {row[0] for row in CHECKLIST}:
            raise KeyError(item_key)
        if state not in {"not_started", "checked", "needs_help"}:
            raise ValueError("Unsupported checklist state")
        clean_note = _reject_secrets(note)[:1000]
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_broadcast_checklist(user_id,item_key,state,note,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id,item_key) DO UPDATE SET state=excluded.state,note=excluded.note,updated_at=excluded.updated_at""",
                (user_id, item_key, state, clean_note, _now()),
            )
        return next(row for row in self.checklist(user_id) if row["key"] == item_key)

    def add_network_test(self, user_id: str, body: NetworkTestRequest) -> dict:
        note = _reject_secrets(body.connection_note)[:1000]
        with self._connect() as con:
            cursor = con.execute(
                """INSERT INTO esp_network_tests
                   (user_id,download_mbps,upload_mbps,latency_ms,jitter_ms,packet_loss_percent,connection_note,source,created_at)
                   VALUES (?,?,?,?,?,?,?,'member_reported',?)""",
                (
                    user_id,
                    body.download_mbps,
                    body.upload_mbps,
                    body.latency_ms,
                    body.jitter_ms,
                    body.packet_loss_percent,
                    note,
                    _now(),
                ),
            )
            row = con.execute("SELECT * FROM esp_network_tests WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else {}

    def network_tests(self, user_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM esp_network_tests WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def readiness(self, user_id: str) -> dict:
        checks = self.checklist(user_id)
        tests = self.network_tests(user_id, 1)
        checked = sum(1 for row in checks if row["state"] == "checked")
        needs_help = [row["title"] for row in checks if row["state"] == "needs_help"]
        reasons: list[str] = []
        positives: list[str] = []
        if checked == len(checks):
            positives.append("All broadcast-preflight checklist items are checked")
        else:
            reasons.append(f"{len(checks) - checked} preflight checklist item(s) are not yet checked")
        if needs_help:
            reasons.append("Help requested for: " + ", ".join(needs_help[:3]))
        if not tests:
            reasons.append("No member-reported network diagnostic has been recorded yet")
        else:
            latest = tests[0]
            # Internal conservative diagnostics only; these are not stated as TikTok requirements.
            if latest.get("packet_loss_percent") is not None and float(latest["packet_loss_percent"]) > 2:
                reasons.append("Latest reported packet loss is above the ESP internal diagnostic watch level")
            if latest.get("upload_mbps") is not None and float(latest["upload_mbps"]) < 5:
                reasons.append("Latest reported upload speed is below the ESP internal diagnostic watch level")
            if not reasons or all("checklist" in item for item in reasons):
                positives.append("Latest reported network diagnostic has no ESP internal watch flag")
        state = "needs_help" if needs_help else "watch" if reasons else "ready"
        return {
            "state": state,
            "checked": checked,
            "total": len(checks),
            "reasons": reasons,
            "positives": positives,
            "diagnostic_only": True,
            "not_platform_requirement": True,
            "remote_control_enabled": False,
            "credentials_stored": False,
        }


broadcast = BroadcastTechStore()


@router.get("/command-center/api/broadcast-tech")
def broadcast_tech_state(request: Request):
    member, _membership = require_esp_hub_member(request)
    return {
        "profile": broadcast.profile(member.user_id),
        "checklist": broadcast.checklist(member.user_id),
        "network_tests": broadcast.network_tests(member.user_id),
        "readiness": broadcast.readiness(member.user_id),
        "security": {
            "credentials_stored": False,
            "remote_control_enabled": False,
            "passwords_requested": False,
        },
    }


@router.put("/command-center/api/broadcast-tech/profile")
def save_broadcast_profile(body: BroadcastProfileRequest, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {"profile": broadcast.save_profile(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/command-center/api/broadcast-tech/checklist/{item_key}")
def save_checklist_item(item_key: str, body: ChecklistRequest, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {"item": broadcast.set_checklist(member.user_id, item_key, state=body.state, note=body.note)}
    except KeyError as exc:
        raise HTTPException(404, "Broadcast checklist item not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/broadcast-tech/network-tests")
def save_network_test(body: NetworkTestRequest, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {"network_test": broadcast.add_network_test(member.user_id, body), "readiness": broadcast.readiness(member.user_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


CSS = r"""
:root{--line:#ffffff1d;--muted:#c2bfd1;--gold:#efc86f;--violet:#9f70ff;--good:#78dfa7;--bad:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42165e,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.2rem);letter-spacing:-.055em;line-height:.94;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn,.field{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#09070f;color:#fff;font:inherit}.btn{font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.card{border:1px solid var(--line);border-radius:15px;background:#14101ceb;padding:14px;margin:9px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field{width:100%;margin:5px 0}.check{border:1px solid var(--line);border-radius:12px;padding:10px;margin:7px 0}.state{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:780px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/broadcast-tech',$=id=>document.getElementById(id);let state=null;function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'var(--bad)':''}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
function render(){const p=state.profile||{},r=state.readiness||{};$('setup').value=p.setup_type||'other';$('os').value=p.operating_system||'';$('internet').value=p.internet_type||'';$('mic').value=p.microphone||'';$('interface').value=p.audio_interface||'';$('camera').value=p.camera||'';$('capture').value=p.capture_card||'';$('notes').value=p.notes||'';$('consent').checked=!!p.consent_store_setup;$('readiness').innerHTML=`<span class="state">${esc(r.state||'watch')}</span> · ${r.checked||0}/${r.total||0} checks complete<br><span class="muted">${esc((r.reasons||[]).join(' · ')||(r.positives||[]).join(' · '))}</span>`;$('checklist').innerHTML=(state.checklist||[]).map(x=>`<div class="check"><div class="row"><div><b>${esc(x.title)}</b><p class="muted">${esc(x.guidance)}</p></div><select class="field" style="width:auto" onchange="setCheck('${esc(x.key)}',this.value)"><option value="not_started" ${x.state==='not_started'?'selected':''}>Not started</option><option value="checked" ${x.state==='checked'?'selected':''}>Checked</option><option value="needs_help" ${x.state==='needs_help'?'selected':''}>Needs help</option></select></div></div>`).join('');$('tests').innerHTML=(state.network_tests||[]).slice(0,5).map(t=>`<p class="muted">${esc((t.created_at||'').slice(0,16))} · Down ${t.download_mbps??'—'} · Up ${t.upload_mbps??'—'} Mbps · Latency ${t.latency_ms??'—'} ms · Loss ${t.packet_loss_percent??'—'}%</p>`).join('')||'<p class="muted">No network diagnostics recorded.</p>'}
async function load(){try{state=await req(API);render()}catch(e){note(e.message,true)}}$('saveProfile').onclick=async()=>{try{await req(API+'/profile',{method:'PUT',body:JSON.stringify({setup_type:$('setup').value,operating_system:$('os').value,internet_type:$('internet').value,microphone:$('mic').value,audio_interface:$('interface').value,camera:$('camera').value,capture_card:$('capture').value,notes:$('notes').value,consent_store_setup:$('consent').checked})});note('Broadcast setup saved according to your consent choice.');await load()}catch(e){note(e.message,true)}};async function setCheck(key,value){try{await req(API+'/checklist/'+encodeURIComponent(key),{method:'PUT',body:JSON.stringify({state:value,note:''})});await load()}catch(e){note(e.message,true)}}$('saveTest').onclick=async()=>{const num=id=>$(id).value===''?null:Number($(id).value);try{await req(API+'/network-tests',{method:'POST',body:JSON.stringify({download_mbps:num('down'),upload_mbps:num('up'),latency_ms:num('latency'),jitter_ms:num('jitter'),packet_loss_percent:num('loss'),connection_note:''})});note('Member-reported network diagnostic saved.');await load()}catch(e){note(e.message,true)}};load();
"""


@router.get("/command-center/broadcast-tech", response_class=HTMLResponse, include_in_schema=False)
def broadcast_tech_page(request: Request):
    require_esp_hub_member(request)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Pro Broadcast & Tech Desk</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Creator Technology</div><h1>Pro Broadcast & Tech Desk</h1><p class='muted'>Plan and verify your LIVE setup without sharing account credentials. ESP does not store your TikTok password, stream key, OAuth token or API secrets here and does not remotely control your computer.</p></div><div><a class='btn' href='/command-center/support'>Tech support case</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></div></div><div id='notice' class='notice'></div><section class='card'><h2>Readiness</h2><div id='readiness'>Loading…</div><p class='muted'>Readiness is internal diagnostic guidance, not a TikTok platform requirement or guarantee of LIVE quality.</p></section><section class='grid'><div class='card'><h2>Consented setup profile</h2><select id='setup' class='field'><option value='mobile'>Mobile</option><option value='live_studio'>TikTok LIVE Studio</option><option value='obs'>OBS</option><option value='hybrid'>Hybrid</option><option value='other'>Other</option></select><input id='os' class='field' placeholder='Operating system'><input id='internet' class='field' placeholder='Internet / connection type'><input id='mic' class='field' placeholder='Microphone'><input id='interface' class='field' placeholder='Audio interface / mixer'><input id='camera' class='field' placeholder='Camera'><input id='capture' class='field' placeholder='Capture card'><textarea id='notes' class='field' placeholder='Non-secret setup notes'></textarea><label><input id='consent' type='checkbox'> I consent to ESP storing this setup metadata for technical support.</label><br><button id='saveProfile' class='btn primary'>Save setup</button></div><div class='card'><h2>Broadcast preflight</h2><div id='checklist'></div></div></section><section class='card'><h2>Member-reported network diagnostic</h2><div class='row'><input id='down' class='field' style='width:140px' type='number' step='0.1' placeholder='Download Mbps'><input id='up' class='field' style='width:140px' type='number' step='0.1' placeholder='Upload Mbps'><input id='latency' class='field' style='width:140px' type='number' step='0.1' placeholder='Latency ms'><input id='jitter' class='field' style='width:140px' type='number' step='0.1' placeholder='Jitter ms'><input id='loss' class='field' style='width:140px' type='number' step='0.1' placeholder='Packet loss %'><button id='saveTest' class='btn'>Record diagnostic</button></div><div id='tests'></div></section></main><script>{SCRIPT}</script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "BroadcastTechStore", "broadcast", "CHECKLIST"]
