from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME
from .owner_identity import owner_actor, owner_session_authorized

router = APIRouter()
accounts = AccountStore()

ALLOWED_SUBSYSTEMS = {
    "core", "aura", "esp", "music", "video", "image", "game_forge",
    "billing", "privacy", "safety", "aura_sec", "deployment", "other",
}
ALLOWED_CHANGE_TYPES = {"new_feature", "enhancement", "bug_fix", "maintenance", "security", "content"}
ALLOWED_EXECUTION_STATES = {"planned", "approved", "queued", "running", "validated", "failed", "cancelled"}
PROTECTED_PATH_PREFIXES = (".github/workflows/", "deploy/", "infra/", "migrations/", "scripts/")
HIGH_RISK_TERMS = {
    "authentication", "authorization", "password", "secret", "token", "billing", "payment", "refund",
    "database migration", "delete data", "privacy", "security", "aura sec", "role", "permission", "production",
    "deploy", "webhook", "encryption", "key rotation",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", (value or "").strip())[:limit]


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value[:55] or "owner-update"


def _risk_for(text: str, subsystem: str, change_type: str) -> tuple[str, list[str]]:
    lower = text.lower()
    reasons = sorted(term for term in HIGH_RISK_TERMS if term in lower)
    if subsystem in {"billing", "privacy", "safety", "aura_sec", "deployment"}:
        reasons.append(f"sensitive subsystem: {subsystem}")
    if change_type == "security":
        reasons.append("security change")
    reasons = sorted(set(reasons))
    if len(reasons) >= 2:
        return "high", reasons
    if reasons:
        return "medium", reasons
    return "low", []


class OwnerFeatureWorkshopStore:
    """Owner change-control workspace for maintaining the Command Center with Aura.

    The browser never runs shell commands, edits the repository directly, merges PRs or deploys
    production. It records owner intent, creates bounded implementation plans and can hand an
    approved plan to a separately configured trusted maintenance worker. This preserves the same
    branch/PR/CI/security-gate discipline used by the engineering build.
    """

    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or accounts
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS owner_feature_changes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    subsystem TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    risk_level TEXT NOT NULL,
                    risk_reasons_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'planned',
                    branch_name TEXT NOT NULL,
                    acceptance_json TEXT NOT NULL DEFAULT '[]',
                    aura_plan_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owner_feature_change_events (
                    id TEXT PRIMARY KEY,
                    change_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(change_id) REFERENCES owner_feature_changes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS owner_feature_execution_requests (
                    id TEXT PRIMARY KEY,
                    change_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued',
                    payload_digest TEXT NOT NULL,
                    worker_ref TEXT,
                    pr_number INTEGER,
                    head_sha TEXT,
                    ci_state TEXT,
                    security_state TEXT,
                    deployment_state TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(change_id) REFERENCES owner_feature_changes(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_owner_feature_changes_status ON owner_feature_changes(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_owner_feature_events_change ON owner_feature_change_events(change_id, created_at DESC);
            """)

    def _event(self, con: sqlite3.Connection, change_id: str, event_type: str, actor: str, details: dict | None = None) -> None:
        con.execute(
            "INSERT INTO owner_feature_change_events(id,change_id,actor,event_type,details_json,created_at) VALUES (?,?,?,?,?,?)",
            (uuid4().hex, change_id, actor[:120], event_type[:80], json.dumps(details or {}, sort_keys=True), _now()),
        )

    def aura_plan(self, *, title: str, request_text: str, subsystem: str, change_type: str, acceptance: list[str]) -> dict:
        risk, reasons = _risk_for(f"{title}\n{request_text}", subsystem, change_type)
        tests = ["focused regression tests for the changed behavior", "authorization/tenant negative tests when access is affected"]
        if subsystem in {"billing", "privacy", "safety", "aura_sec", "deployment"} or risk == "high":
            tests.extend(["exact-head Security Gates", "full Command Center CI before integration"])
        else:
            tests.append("full Command Center CI if shared routes, auth, schemas or aggregate app composition change")
        return {
            "summary": f"Implement {title} in the existing {subsystem.replace('_',' ')} system without creating a duplicate architecture.",
            "principles": [
                "inspect the current repository before changing code",
                "reuse existing authentication, data models and shared services",
                "make the smallest coherent change",
                "preserve server-authoritative permissions and tenant isolation",
                "add regression tests and keep production claims evidence-based",
                "use a focused feature branch and pull request; never edit main directly",
            ],
            "implementation_steps": [
                "locate the authoritative existing routes/services/models for this capability",
                "identify shared-file and cross-stream impact before editing",
                "implement the requested behavior behind existing authorization boundaries",
                "add or update tests for success, failure and unauthorized cases",
                "run compile, focused tests, Security Gates when relevant, then full CI",
                "open/review a PR into development/full-site-build and integrate only when green",
            ],
            "acceptance_criteria": acceptance,
            "test_plan": tests,
            "risk_level": risk,
            "risk_reasons": reasons,
            "requires_human_release_approval": risk in {"medium", "high"},
            "browser_executes_code": False,
            "production_auto_deploy": False,
        }

    def create_change(self, *, title: str, request_text: str, subsystem: str, change_type: str,
                      priority: str = "normal", acceptance_text: str = "", actor: str = "ESP Owner") -> dict:
        title = _clean(title, 160)
        request_text = _clean(request_text, 12000)
        subsystem = (subsystem or "other").strip().lower()
        change_type = (change_type or "enhancement").strip().lower()
        priority = (priority or "normal").strip().lower()
        if len(title) < 3 or len(request_text) < 8:
            raise ValueError("Add a clear title and describe the requested update")
        if subsystem not in ALLOWED_SUBSYSTEMS:
            raise ValueError("Unknown subsystem")
        if change_type not in ALLOWED_CHANGE_TYPES:
            raise ValueError("Unknown change type")
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ValueError("Unknown priority")
        acceptance = [_clean(line.lstrip("-• "), 500) for line in acceptance_text.splitlines() if _clean(line.lstrip("-• "), 500)][:30]
        if not acceptance:
            acceptance = ["Requested behavior works for an authorized user", "Existing related behavior remains regression-safe"]
        plan = self.aura_plan(title=title, request_text=request_text, subsystem=subsystem, change_type=change_type, acceptance=acceptance)
        actor = owner_actor(actor)[:120]
        change_id = uuid4().hex
        branch = f"owner-update/{_slug(title)}-{change_id[:8]}"
        now = _now()
        with self._connect() as con:
            con.execute("""INSERT INTO owner_feature_changes
                (id,title,request_text,subsystem,change_type,priority,risk_level,risk_reasons_json,status,branch_name,
                 acceptance_json,aura_plan_json,created_at,created_by,updated_at,updated_by)
                VALUES (?,?,?,?,?,?,?,?, 'planned',?,?,?,?,?,?,?)""",
                (change_id, title, request_text, subsystem, change_type, priority, plan["risk_level"],
                 json.dumps(plan["risk_reasons"]), branch, json.dumps(acceptance), json.dumps(plan), now, actor, now, actor),
            )
            self._event(con, change_id, "change_created", actor, {"risk": plan["risk_level"], "branch": branch})
        return self.get(change_id) or {}

    def get(self, change_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM owner_feature_changes WHERE id=?", (change_id,)).fetchone()
            if not row:
                return None
            events = con.execute("SELECT * FROM owner_feature_change_events WHERE change_id=? ORDER BY created_at DESC", (change_id,)).fetchall()
            executions = con.execute("SELECT * FROM owner_feature_execution_requests WHERE change_id=? ORDER BY created_at DESC", (change_id,)).fetchall()
        out = dict(row)
        for src, dst, default in (("risk_reasons_json", "risk_reasons", []), ("acceptance_json", "acceptance", []), ("aura_plan_json", "aura_plan", {})):
            try:
                out[dst] = json.loads(out.pop(src) or json.dumps(default))
            except Exception:
                out[dst] = default
        out["events"] = [{**dict(e), "details": json.loads(e["details_json"] or "{}")} for e in events]
        out["executions"] = [dict(e) for e in executions]
        return out

    def list(self, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM owner_feature_changes ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["risk_reasons"] = json.loads(item.pop("risk_reasons_json") or "[]")
            item.pop("acceptance_json", None)
            item.pop("aura_plan_json", None)
            result.append(item)
        return result

    def approve(self, change_id: str, actor: str = "ESP Owner") -> dict:
        item = self.get(change_id)
        if not item:
            raise ValueError("Change request not found")
        if item["status"] not in {"planned", "failed", "cancelled"}:
            raise ValueError("Only planned, failed or cancelled requests can be approved")
        actor = owner_actor(actor)[:120]
        with self._connect() as con:
            con.execute("UPDATE owner_feature_changes SET status='approved',updated_at=?,updated_by=? WHERE id=?", (_now(), actor, change_id))
            self._event(con, change_id, "owner_approved", actor, {"risk": item["risk_level"]})
        return self.get(change_id) or {}

    def queue_execution(self, change_id: str, actor: str = "ESP Owner") -> dict:
        item = self.get(change_id)
        if not item:
            raise ValueError("Change request not found")
        if item["status"] != "approved":
            raise ValueError("Owner approval is required before implementation can be queued")
        payload = {
            "change_id": item["id"], "title": item["title"], "request": item["request_text"],
            "subsystem": item["subsystem"], "change_type": item["change_type"], "priority": item["priority"],
            "branch": item["branch_name"], "acceptance": item["acceptance"], "aura_plan": item["aura_plan"],
            "target_branch": "development/full-site-build", "forbid_main": True,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        execution_id = uuid4().hex
        actor = owner_actor(actor)[:120]
        now = _now()
        with self._connect() as con:
            con.execute("INSERT INTO owner_feature_execution_requests(id,change_id,state,payload_digest,created_at,updated_at) VALUES (?,?,'queued',?,?,?)",
                        (execution_id, change_id, digest, now, now))
            con.execute("UPDATE owner_feature_changes SET status='queued',updated_at=?,updated_by=? WHERE id=?", (now, actor, change_id))
            self._event(con, change_id, "implementation_queued", actor, {"execution_id": execution_id, "payload_digest": digest})
        return {"execution_id": execution_id, "payload": payload, "payload_digest": digest, "worker_configured": bool(os.getenv("LSS_OWNER_MAINTENANCE_WORKER_TOKEN"))}

    def worker_payload(self, execution_id: str, token: str | None) -> dict:
        configured = (os.getenv("LSS_OWNER_MAINTENANCE_WORKER_TOKEN") or "").strip()
        if not configured or not token or not secrets.compare_digest(configured, token):
            raise PermissionError("Trusted maintenance worker authorization required")
        with self._connect() as con:
            row = con.execute("SELECT e.*,c.* FROM owner_feature_execution_requests e JOIN owner_feature_changes c ON c.id=e.change_id WHERE e.id=?", (execution_id,)).fetchone()
        if not row:
            raise ValueError("Execution request not found")
        item = self.get(row["change_id"])
        assert item is not None
        return {
            "change_id": item["id"], "execution_id": execution_id, "title": item["title"], "request": item["request_text"],
            "subsystem": item["subsystem"], "change_type": item["change_type"], "priority": item["priority"],
            "branch": item["branch_name"], "target_branch": "development/full-site-build", "forbid_main": True,
            "acceptance": item["acceptance"], "aura_plan": item["aura_plan"], "payload_digest": row["payload_digest"],
        }

    def worker_report(self, execution_id: str, *, token: str | None, state: str, worker_ref: str = "",
                      pr_number: int | None = None, head_sha: str = "", ci_state: str = "",
                      security_state: str = "", deployment_state: str = "") -> dict:
        configured = (os.getenv("LSS_OWNER_MAINTENANCE_WORKER_TOKEN") or "").strip()
        if not configured or not token or not secrets.compare_digest(configured, token):
            raise PermissionError("Trusted maintenance worker authorization required")
        state = (state or "").lower()
        if state not in {"running", "validated", "failed", "cancelled"}:
            raise ValueError("Invalid worker state")
        if head_sha and not re.fullmatch(r"[0-9a-f]{40}", head_sha.lower()):
            raise ValueError("Invalid Git commit SHA")
        with self._connect() as con:
            row = con.execute("SELECT change_id FROM owner_feature_execution_requests WHERE id=?", (execution_id,)).fetchone()
            if not row:
                raise ValueError("Execution request not found")
            con.execute("""UPDATE owner_feature_execution_requests SET state=?,worker_ref=?,pr_number=?,head_sha=?,ci_state=?,security_state=?,deployment_state=?,updated_at=? WHERE id=?""",
                        (state, _clean(worker_ref, 240), pr_number, head_sha.lower(), _clean(ci_state, 40), _clean(security_state, 40), _clean(deployment_state, 40), _now(), execution_id))
            con.execute("UPDATE owner_feature_changes SET status=?,updated_at=? WHERE id=?", (state, _now(), row["change_id"]))
            self._event(con, row["change_id"], "worker_report", "trusted-maintenance-worker", {"state": state, "pr_number": pr_number, "head_sha": head_sha, "ci": ci_state, "security": security_state})
        return self.get(row["change_id"]) or {}


workshop = OwnerFeatureWorkshopStore()


def _page(body: str, title: str) -> HTMLResponse:
    css = """body{margin:0;background:#09060f;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1300px,calc(100% - 28px));margin:auto;padding:28px 0 60px}.row{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.eyebrow{color:#f4bd68;text-transform:uppercase;letter-spacing:.14em;font-size:.74rem;font-weight:900}h1{font-size:clamp(2.5rem,6vw,4.8rem);margin:.1em 0}.muted{color:#cec3d7;line-height:1.6}.card{background:#17101fee;border:1px solid #ffffff20;border-radius:20px;padding:18px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.btn,button{display:inline-block;border:1px solid #ffffff24;background:#ffffff0c;color:#fff;border-radius:10px;padding:10px 13px;font-weight:800;text-decoration:none;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,#f3bd68,#9c75ff);color:#160b1d}input,textarea,select{width:100%;box-sizing:border-box;background:#0c0812;color:#fff;border:1px solid #ffffff24;border-radius:10px;padding:11px;margin:6px 0 12px}textarea{min-height:140px}.pill{display:inline-block;border:1px solid #ffffff22;border-radius:999px;padding:4px 8px;font-size:.75rem;margin:2px}.risk-high{color:#ff9bad}.risk-medium{color:#ffd47e}.risk-low{color:#82dfaa}pre{white-space:pre-wrap;color:#ded3e7}@media(max-width:850px){.grid{grid-template-columns:1fr}}"""
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)} — {escape(PRODUCT_FULL_NAME)}</title><style>{css}</style></head><body><main class='wrap'>{body}</main></body></html>")


@router.get("/owner/updates", response_class=HTMLResponse, include_in_schema=False)
@router.get("/owner/feature-workshop", response_class=HTMLResponse, include_in_schema=False)
def feature_workshop(request: Request, message: str = ""):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = workshop.list()
    cards = "".join(
        f"<div class='card'><div class='row'><div><span class='pill'>{escape(r['subsystem'].replace('_',' ').title())}</span><span class='pill'>{escape(r['change_type'].replace('_',' ').title())}</span><span class='pill risk-{escape(r['risk_level'])}'>{escape(r['risk_level'].upper())} risk</span></div><b>{escape(r['status'].replace('_',' ').title())}</b></div><h3>{escape(r['title'])}</h3><p class='muted'>{escape(r['request_text'][:300])}</p><a class='btn' href='/owner/updates/{escape(r['id'], quote=True)}'>Open with Aura</a></div>"
        for r in rows
    ) or "<div class='card'><p class='muted'>No owner update requests yet.</p></div>"
    flash = f"<div class='card'>{escape(message)}</div>" if message else ""
    body = f"""<div class='row'><div><div class='eyebrow'>Mary / Kev · Aura Maintenance Studio</div><h1>Updates & New Features</h1><p class='muted'>Describe a new feature, improvement, fix or maintenance need in plain language. Aura turns it into a controlled engineering plan using the existing architecture, branch, PR, CI and Security Gate process.</p></div><div><a class='btn' href='/owner/dashboard'>Owner Command Center</a><a class='btn' href='/owner/support'>Support</a></div></div>{flash}
<section class='card'><div class='eyebrow'>Create an update request</div><form method='post' action='/owner/updates'><label>Title</label><input name='title' required maxlength='160' placeholder='Example: Add bulk creator campaign assignment'><label>Tell Aura what you want changed</label><textarea id='request_text' name='request_text' required maxlength='12000' placeholder='Describe the feature exactly as you would describe it to Aura in chat...'></textarea><button type='button' onclick='voice()'>🎙 Speak to Aura</button><div class='grid'><div><label>System</label><select name='subsystem'>{''.join(f"<option value='{s}'>{escape(s.replace('_',' ').title())}</option>" for s in sorted(ALLOWED_SUBSYSTEMS))}</select></div><div><label>Change type</label><select name='change_type'>{''.join(f"<option value='{c}'>{escape(c.replace('_',' ').title())}</option>" for c in sorted(ALLOWED_CHANGE_TYPES))}</select></div><div><label>Priority</label><select name='priority'><option>normal</option><option>low</option><option>high</option><option>urgent</option></select></div></div><label>Acceptance criteria (one per line, optional)</label><textarea name='acceptance_text' placeholder='What must be true before Mary/Kev consider this complete?'></textarea><button class='primary'>Ask Aura to plan update</button></form><p class='muted'><b>Safety:</b> this page does not expose a terminal or edit production code in the web process. Approved plans can only be handed to a separately trusted maintenance worker, and integration still requires branch/PR/test/security review.</p></section><section><div class='eyebrow'>Change queue</div>{cards}</section>
<script>function voice(){{const R=window.SpeechRecognition||window.webkitSpeechRecognition;if(!R){{alert('Voice input is not available in this browser.');return}}const r=new R();r.lang=navigator.language||'en-GB';r.interimResults=false;r.onresult=e=>{{const box=document.getElementById('request_text');box.value=(box.value+' '+e.results[0][0].transcript).trim()}};r.start()}}</script>"""
    return _page(body, "Owner Updates & New Features")


@router.post("/owner/updates", include_in_schema=False)
def create_update(request: Request, title: str = Form(...), request_text: str = Form(...), subsystem: str = Form("other"),
                  change_type: str = Form("enhancement"), priority: str = Form("normal"), acceptance_text: str = Form("")):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        item = workshop.create_change(title=title, request_text=request_text, subsystem=subsystem, change_type=change_type,
                                      priority=priority, acceptance_text=acceptance_text)
    except ValueError as exc:
        return feature_workshop(request, str(exc))
    return RedirectResponse(f"/owner/updates/{item['id']}", status_code=303)


@router.get("/owner/updates/{change_id}", response_class=HTMLResponse, include_in_schema=False)
def update_detail(change_id: str, request: Request, message: str = ""):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    item = workshop.get(change_id)
    if not item:
        raise HTTPException(404, "Update request not found")
    plan = item["aura_plan"]
    criteria = "".join(f"<li>{escape(x)}</li>" for x in item["acceptance"])
    steps = "".join(f"<li>{escape(x)}</li>" for x in plan.get("implementation_steps", []))
    tests = "".join(f"<li>{escape(x)}</li>" for x in plan.get("test_plan", []))
    actions = ""
    if item["status"] in {"planned", "failed", "cancelled"}:
        actions += f"<form method='post' action='/owner/updates/{escape(change_id, quote=True)}/approve' style='display:inline'><button class='primary'>Mary/Kev approve plan</button></form>"
    if item["status"] == "approved":
        actions += f"<form method='post' action='/owner/updates/{escape(change_id, quote=True)}/queue' style='display:inline'><button class='primary'>Queue implementation</button></form>"
    executions = "".join(f"<div class='card'><b>{escape(e['state'].title())}</b> · PR {escape(str(e.get('pr_number') or '—'))}<br><span class='muted'>CI {escape(e.get('ci_state') or '—')} · Security {escape(e.get('security_state') or '—')} · Head {escape((e.get('head_sha') or '—')[:12])}</span></div>" for e in item["executions"]) or "<p class='muted'>No implementation run yet.</p>"
    flash = f"<div class='card'>{escape(message)}</div>" if message else ""
    body = f"""<div class='row'><div><div class='eyebrow'>Aura change plan</div><h1>{escape(item['title'])}</h1><p class='muted'>{escape(item['request_text'])}</p></div><a class='btn' href='/owner/updates'>All updates</a></div>{flash}<section class='grid'><div class='card'><b>Status</b><h2>{escape(item['status'].replace('_',' ').title())}</h2></div><div class='card'><b>Risk</b><h2 class='risk-{escape(item['risk_level'])}'>{escape(item['risk_level'].upper())}</h2></div><div class='card'><b>Planned branch</b><p>{escape(item['branch_name'])}</p></div></section><section class='card'><div class='eyebrow'>Aura engineering plan</div><p>{escape(plan.get('summary',''))}</p><h3>Implementation</h3><ol>{steps}</ol><h3>Acceptance criteria</h3><ul>{criteria}</ul><h3>Validation</h3><ul>{tests}</ul><p class='muted'>Browser executes code: <b>No</b> · Production auto-deploy: <b>No</b> · Main direct edit: <b>Forbidden</b></p><div class='row'>{actions}</div></section><section><div class='eyebrow'>Implementation evidence</div>{executions}</section>"""
    return _page(body, "Aura Update Plan")


@router.post("/owner/updates/{change_id}/approve", include_in_schema=False)
def approve_update(change_id: str, request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        workshop.approve(change_id)
    except ValueError as exc:
        return update_detail(change_id, request, str(exc))
    return RedirectResponse(f"/owner/updates/{change_id}", status_code=303)


@router.post("/owner/updates/{change_id}/queue", include_in_schema=False)
def queue_update(change_id: str, request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        result = workshop.queue_execution(change_id)
    except ValueError as exc:
        return update_detail(change_id, request, str(exc))
    note = "Implementation queued for the trusted maintenance worker." if result["worker_configured"] else "Plan queued, but no trusted maintenance worker is configured yet; no code has been executed."
    return RedirectResponse(f"/owner/updates/{change_id}?message={escape(note, quote=True)}", status_code=303)


@router.get("/owner-maintenance/worker/{execution_id}", include_in_schema=False)
def maintenance_worker_payload(execution_id: str, x_owner_maintenance_token: str | None = Header(default=None)):
    try:
        return workshop.worker_payload(execution_id, x_owner_maintenance_token)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/owner-maintenance/worker/{execution_id}/report", include_in_schema=False)
def maintenance_worker_report(execution_id: str, state: str = Form(...), worker_ref: str = Form(""),
                              pr_number: int | None = Form(None), head_sha: str = Form(""), ci_state: str = Form(""),
                              security_state: str = Form(""), deployment_state: str = Form(""),
                              x_owner_maintenance_token: str | None = Header(default=None)):
    try:
        return workshop.worker_report(execution_id, token=x_owner_maintenance_token, state=state, worker_ref=worker_ref,
                                      pr_number=pr_number, head_sha=head_sha, ci_state=ci_state,
                                      security_state=security_state, deployment_state=deployment_state)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
