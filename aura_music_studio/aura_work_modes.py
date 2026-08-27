from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_chat_store import AuraChatStore

AuraWorkMode = Literal["ask", "plan", "agent"]
router = APIRouter(tags=["Aura Work Modes"])
store = AuraChatStore()

_ACTIVE_MODE: ContextVar[str] = ContextVar("aura_work_mode", default="agent")
_ACTIVE_SCOPE: ContextVar[tuple[AuraChatStore, str, str] | None] = ContextVar("aura_work_scope", default=None)
_APPROVED_PLAN: ContextVar[bool] = ContextVar("aura_approved_plan", default=False)
_LAST_SAVED_PLAN: ContextVar[dict | None] = ContextVar("aura_last_saved_plan", default=None)
_INSTALLED = False
_CORE_SIGNATURE = "You are Aura, the general AI co-creator and operating intelligence inside Pulsar-Frequency House"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def work_mode_instruction(mode: str | None = None) -> str:
    value = (mode or _ACTIVE_MODE.get() or "agent").lower()
    if value == "ask":
        return (
            "Aura Ask work mode is active. Inspect, research, explain and advise, but do not change project state. "
            "Project-changing tools are blocked by the runtime in this mode."
        )
    if value == "plan":
        return (
            "Aura Plan work mode is active. Inspect available context and construct a concrete executable plan. "
            "Read-only tools may run for evidence, but project-changing steps are saved as an immutable work plan and are not executed until the member explicitly approves the plan and uses Agent mode."
        )
    return (
        "Aura Agent work mode is active. You may use permissioned tools when the member explicitly requests an action. "
        "An approved saved work plan may execute only its frozen tool list; all ordinary rights, membership, project, provider and safety gates still apply."
    )


def active_work_mode() -> AuraWorkMode:
    value = (_ACTIVE_MODE.get() or "agent").lower()
    return value if value in {"ask", "plan", "agent"} else "agent"  # type: ignore[return-value]


def ensure_work_mode_schema(chat_store: AuraChatStore) -> None:
    with chat_store._connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS aura_chat_work_modes (
                thread_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'agent',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS aura_work_plans (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                project_name TEXT,
                calls_json TEXT NOT NULL,
                plan_hash TEXT NOT NULL,
                approved_hash TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                results_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approved_at TEXT,
                executed_at TEXT,
                FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_aura_work_plans_thread
                ON aura_work_plans(user_id,thread_id,updated_at DESC);
            """
        )


def get_work_mode(chat_store: AuraChatStore, user_id: str, thread_id: str) -> AuraWorkMode:
    ensure_work_mode_schema(chat_store)
    if not chat_store.thread(user_id, thread_id):
        raise KeyError(thread_id)
    with chat_store._connect() as con:
        row = con.execute(
            "SELECT mode FROM aura_chat_work_modes WHERE thread_id=? AND user_id=?",
            (thread_id, user_id),
        ).fetchone()
    value = str(row["mode"] if row else "agent").lower()
    return value if value in {"ask", "plan", "agent"} else "agent"  # type: ignore[return-value]


def set_work_mode(chat_store: AuraChatStore, user_id: str, thread_id: str, mode: str) -> AuraWorkMode:
    value = (mode or "").strip().lower()
    if value not in {"ask", "plan", "agent"}:
        raise ValueError("Aura work mode must be ask, plan or agent")
    ensure_work_mode_schema(chat_store)
    if not chat_store.thread(user_id, thread_id):
        raise KeyError(thread_id)
    with chat_store._connect() as con:
        con.execute(
            """INSERT INTO aura_chat_work_modes(thread_id,user_id,mode,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(thread_id) DO UPDATE SET mode=excluded.mode,user_id=excluded.user_id,updated_at=excluded.updated_at""",
            (thread_id, user_id, value, _now()),
        )
    _ACTIVE_MODE.set(value)
    _ACTIVE_SCOPE.set((chat_store, user_id, thread_id))
    return value  # type: ignore[return-value]


def activate_work_mode_scope(chat_store: AuraChatStore, user_id: str, thread_id: str) -> AuraWorkMode:
    mode = get_work_mode(chat_store, user_id, thread_id)
    _ACTIVE_MODE.set(mode)
    _ACTIVE_SCOPE.set((chat_store, user_id, thread_id))
    return mode


def detect_work_mode_command(text: str) -> AuraWorkMode | None:
    clean = " ".join((text or "").strip().lower().split())
    patterns = [
        r"^(?:aura[,:]?\s*)?(?:switch|set|change|use|go)?\s*(?:to\s+)?(ask|plan|agent)\s+(?:work\s+)?mode(?:\s+please)?[.!]?$",
        r"^(?:aura[,:]?\s*)?(ask|plan|agent)\s+mode(?:\s+please)?[.!]?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, clean)
        if match:
            return match.group(1)  # type: ignore[return-value]
    return None


@contextmanager
def work_mode_scope(
    chat_store: AuraChatStore,
    user_id: str,
    thread_id: str,
    *,
    mode: str | None = None,
    approved_plan: bool = False,
) -> Iterator[AuraWorkMode]:
    selected = mode or get_work_mode(chat_store, user_id, thread_id)
    if selected not in {"ask", "plan", "agent"}:
        selected = "agent"
    mode_token = _ACTIVE_MODE.set(selected)
    scope_token = _ACTIVE_SCOPE.set((chat_store, user_id, thread_id))
    approval_token = _APPROVED_PLAN.set(bool(approved_plan))
    saved_token = _LAST_SAVED_PLAN.set(None)
    try:
        yield selected  # type: ignore[misc]
    finally:
        _LAST_SAVED_PLAN.reset(saved_token)
        _APPROVED_PLAN.reset(approval_token)
        _ACTIVE_SCOPE.reset(scope_token)
        _ACTIVE_MODE.reset(mode_token)


def _canonical_plan(objective: str, project_name: str | None, calls: list[dict]) -> str:
    return json.dumps(
        {"objective": objective, "project_name": project_name or "", "calls": calls},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _plan_hash(objective: str, project_name: str | None, calls: list[dict]) -> str:
    return hashlib.sha256(_canonical_plan(objective, project_name, calls).encode("utf-8")).hexdigest()


def _decode_plan(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    try:
        item["calls"] = json.loads(item.pop("calls_json") or "[]")
    except Exception:
        item["calls"] = []
    try:
        item["results"] = json.loads(item.pop("results_json") or "[]")
    except Exception:
        item["results"] = []
    return item


class AuraWorkPlanStore:
    def __init__(self, chat_store: AuraChatStore | None = None):
        self.chat = chat_store or store
        ensure_work_mode_schema(self.chat)

    def create(
        self,
        user_id: str,
        thread_id: str,
        *,
        objective: str,
        project_name: str | None,
        calls: list[dict],
    ) -> dict:
        if not self.chat.thread(user_id, thread_id):
            raise KeyError(thread_id)
        clean_objective = " ".join((objective or "").split())[:6000]
        if not clean_objective:
            raise ValueError("Aura work plan needs an objective")
        validated: list[dict] = []
        for raw in calls[:6]:
            call = tools.ToolCall.model_validate(raw)
            if call.name not in tools._SPEC_BY_NAME:
                raise ValueError(f"Unknown Aura tool in work plan: {call.name}")
            validated.append(call.model_dump(mode="json"))
        if not validated:
            raise ValueError("Aura work plan needs at least one tool step")
        digest = _plan_hash(clean_objective, project_name, validated)
        plan_id = uuid4().hex
        now = _now()
        with self.chat._connect() as con:
            con.execute(
                """INSERT INTO aura_work_plans
                   (id,thread_id,user_id,objective,project_name,calls_json,plan_hash,status,results_json,error,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'draft','[]','',?,?)""",
                (plan_id, thread_id, user_id, clean_objective, project_name, json.dumps(validated, ensure_ascii=False), digest, now, now),
            )
        return self.get(user_id, thread_id, plan_id)

    def get(self, user_id: str, thread_id: str, plan_id: str) -> dict:
        with self.chat._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_work_plans WHERE id=? AND thread_id=? AND user_id=?",
                (plan_id, thread_id, user_id),
            ).fetchone()
        item = _decode_plan(row)
        if not item:
            raise KeyError(plan_id)
        return item

    def list(self, user_id: str, thread_id: str) -> list[dict]:
        if not self.chat.thread(user_id, thread_id):
            raise KeyError(thread_id)
        with self.chat._connect() as con:
            rows = con.execute(
                "SELECT * FROM aura_work_plans WHERE thread_id=? AND user_id=? ORDER BY updated_at DESC LIMIT 50",
                (thread_id, user_id),
            ).fetchall()
        return [_decode_plan(row) or {} for row in rows]

    def approve(self, user_id: str, thread_id: str, plan_id: str) -> dict:
        plan = self.get(user_id, thread_id, plan_id)
        if plan["status"] not in {"draft", "failed"}:
            raise ValueError("Only a draft or failed plan can be approved")
        digest = _plan_hash(plan["objective"], plan.get("project_name"), plan["calls"])
        if digest != plan["plan_hash"]:
            raise ValueError("Aura work plan integrity check failed")
        now = _now()
        with self.chat._connect() as con:
            con.execute(
                """UPDATE aura_work_plans SET status='approved',approved_hash=?,approved_at=?,updated_at=?,error=''
                   WHERE id=? AND thread_id=? AND user_id=?""",
                (digest, now, now, plan_id, thread_id, user_id),
            )
        return self.get(user_id, thread_id, plan_id)

    def cancel(self, user_id: str, thread_id: str, plan_id: str) -> dict:
        plan = self.get(user_id, thread_id, plan_id)
        if plan["status"] not in {"draft", "approved"}:
            raise ValueError("Only a draft or approved plan can be cancelled")
        with self.chat._connect() as con:
            con.execute(
                "UPDATE aura_work_plans SET status='cancelled',updated_at=? WHERE id=? AND thread_id=? AND user_id=?",
                (_now(), plan_id, thread_id, user_id),
            )
        return self.get(user_id, thread_id, plan_id)

    def begin_execution(self, user_id: str, thread_id: str, plan_id: str) -> dict:
        plan = self.get(user_id, thread_id, plan_id)
        digest = _plan_hash(plan["objective"], plan.get("project_name"), plan["calls"])
        if plan["status"] != "approved" or not plan.get("approved_hash"):
            raise PermissionError("Aura work plan must be explicitly approved before execution")
        if digest != plan["plan_hash"] or digest != plan["approved_hash"]:
            raise PermissionError("Approved Aura work plan changed after approval")
        with self.chat._connect() as con:
            cur = con.execute(
                """UPDATE aura_work_plans SET status='running',updated_at=?,results_json='[]',error=''
                   WHERE id=? AND thread_id=? AND user_id=? AND status='approved'""",
                (_now(), plan_id, thread_id, user_id),
            )
            if cur.rowcount != 1:
                raise PermissionError("Aura work plan is no longer approved for execution")
        return self.get(user_id, thread_id, plan_id)

    def finish(self, user_id: str, thread_id: str, plan_id: str, *, results: list[dict], error: str = "") -> dict:
        status = "failed" if error else "completed"
        now = _now()
        with self.chat._connect() as con:
            con.execute(
                """UPDATE aura_work_plans SET status=?,results_json=?,error=?,updated_at=?,executed_at=?
                   WHERE id=? AND thread_id=? AND user_id=? AND status='running'""",
                (status, json.dumps(results, ensure_ascii=False, default=str)[:100000], error[:5000], now, now, plan_id, thread_id, user_id),
            )
        return self.get(user_id, thread_id, plan_id)


plans = AuraWorkPlanStore(store)


class WorkModeRequest(BaseModel):
    mode: AuraWorkMode


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


@router.get("/aura-intelligence/api/threads/{thread_id}/work-mode")
def get_mode_api(thread_id: str, request: Request):
    member = _member(request)
    try:
        mode = get_work_mode(store, member.user_id, thread_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    return {"mode": mode, "write_tools_allowed": mode == "agent", "instruction": work_mode_instruction(mode)}


@router.put("/aura-intelligence/api/threads/{thread_id}/work-mode")
def set_mode_api(thread_id: str, body: WorkModeRequest, request: Request):
    member = _member(request)
    try:
        mode = set_work_mode(store, member.user_id, thread_id, body.mode)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    return {"mode": mode, "write_tools_allowed": mode == "agent", "detail": f"Aura {mode.title()} work mode is now active."}


@router.get("/aura-intelligence/api/threads/{thread_id}/work-plans")
def list_plans_api(thread_id: str, request: Request):
    member = _member(request)
    try:
        return {"plans": plans.list(member.user_id, thread_id)}
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc


@router.post("/aura-intelligence/api/threads/{thread_id}/work-plans/{plan_id}/approve")
def approve_plan_api(thread_id: str, plan_id: str, request: Request):
    member = _member(request)
    try:
        return {"plan": plans.approve(member.user_id, thread_id, plan_id)}
    except KeyError as exc:
        raise HTTPException(404, "Aura work plan not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/aura-intelligence/api/threads/{thread_id}/work-plans/{plan_id}/cancel")
def cancel_plan_api(thread_id: str, plan_id: str, request: Request):
    member = _member(request)
    try:
        return {"plan": plans.cancel(member.user_id, thread_id, plan_id)}
    except KeyError as exc:
        raise HTTPException(404, "Aura work plan not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/aura-intelligence/api/threads/{thread_id}/work-plans/{plan_id}/execute")
def execute_plan_api(thread_id: str, plan_id: str, request: Request):
    member = _member(request)
    try:
        mode = get_work_mode(store, member.user_id, thread_id)
        if mode != "agent":
            raise PermissionError("Switch Aura to Agent work mode before executing an approved plan")
        plan = plans.begin_execution(member.user_id, thread_id, plan_id)
        thread = store.thread(member.user_id, thread_id) or {}
        registry = tools.AuraToolRegistry(
            member=member,
            pinned_project=plan.get("project_name") or thread.get("project_name"),
            web_enabled=bool(thread.get("web_enabled", 1)),
            tools_enabled=bool(thread.get("tools_enabled", 1)),
        )
        results: list[dict] = []
        failure = ""
        with work_mode_scope(store, member.user_id, thread_id, mode="agent", approved_plan=True):
            for raw in plan["calls"][:6]:
                call = tools.ToolCall.model_validate(raw)
                run_id = store.start_tool_run(member.user_id, thread_id, None, call.name, call.arguments)
                try:
                    result = registry.execute(call, latest_user_message=f"Execute approved Aura work plan: {plan['objective']}")
                    store.finish_tool_run(run_id, result=result)
                    results.append({"tool": call.name, "ok": True, "result": result})
                except Exception as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    store.finish_tool_run(run_id, error=failure)
                    results.append({"tool": call.name, "ok": False, "error": failure})
                    break
        completed = plans.finish(member.user_id, thread_id, plan_id, results=results, error=failure)
        return {"plan": completed, "tool_runs": results, "completed": not bool(failure)}
    except KeyError as exc:
        raise HTTPException(404, "Aura work plan not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, tools.ValidationError if hasattr(tools, "ValidationError") else ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _write_tool(name: str) -> bool:
    spec = tools._SPEC_BY_NAME.get(name)
    return bool(spec and spec.write)


def install_aura_work_modes() -> None:
    """Install runtime-enforced Ask/Plan/Agent behavior without replacing Aura's tool ecosystem."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_specs = tools.AuraToolRegistry.specs
    original_execute = tools.AuraToolRegistry.execute
    original_explicit_write = tools._explicit_write_allowed
    original_tool_plan = core.AuraAgent._tool_plan
    original_respond = core.AuraAgent.respond
    original_complete = core.AuraModelClient.complete

    def specs(self: tools.AuraToolRegistry):
        rows = original_specs(self)
        if active_work_mode() == "ask":
            rows = [row for row in rows if not bool(row.get("write"))]
        return rows

    def execute(self: tools.AuraToolRegistry, call: tools.ToolCall, *, latest_user_message: str):
        if _write_tool(call.name) and active_work_mode() != "agent":
            raise PermissionError(f"Aura {active_work_mode().title()} work mode is read-only for project-changing tools")
        return original_execute(self, call, latest_user_message=latest_user_message)

    def explicit_write_allowed(tool_name: str, latest_user_message: str) -> bool:
        if _APPROVED_PLAN.get():
            return True
        return original_explicit_write(tool_name, latest_user_message)

    def tool_plan(self: core.AuraAgent, *, text: str, registry: tools.AuraToolRegistry, project_context: dict | None):
        plan = original_tool_plan(self, text=text, registry=registry, project_context=project_context)
        mode = active_work_mode()
        if mode == "agent":
            return plan
        if mode == "ask":
            read_calls = [call for call in plan.calls if not _write_tool(call.name)]
            return tools.ToolPlan(calls=read_calls, answer_without_tools=plan.answer_without_tools or not read_calls)

        # Plan mode: save the complete frozen plan when it contains a write, but execute only reads now.
        write_calls = [call for call in plan.calls if _write_tool(call.name)]
        if write_calls:
            scope = _ACTIVE_SCOPE.get()
            if scope:
                chat_store, user_id, thread_id = scope
                saved = AuraWorkPlanStore(chat_store).create(
                    user_id,
                    thread_id,
                    objective=text,
                    project_name=registry.pinned_project,
                    calls=[call.model_dump(mode="json") for call in plan.calls],
                )
                _LAST_SAVED_PLAN.set(saved)
        read_calls = [call for call in plan.calls if not _write_tool(call.name)]
        return tools.ToolPlan(calls=read_calls, answer_without_tools=plan.answer_without_tools or not read_calls)

    def respond(self: core.AuraAgent, *, member, thread_id: str, text: str, **kwargs):
        requested = detect_work_mode_command(text)
        if requested:
            set_work_mode(self.store, member.user_id, thread_id, requested)
        with work_mode_scope(self.store, member.user_id, thread_id) as mode:
            result = original_respond(self, member=member, thread_id=thread_id, text=text, **kwargs)
            result["work_mode"] = mode
            saved = _LAST_SAVED_PLAN.get()
            if saved:
                result["work_plan"] = saved
            return result

    def complete(self: core.AuraModelClient, messages: list[dict], **kwargs):
        json_mode = bool(kwargs.get("json_mode", False))
        if not json_mode and messages and messages[0].get("role") == "system" and _CORE_SIGNATURE in str(messages[0].get("content") or ""):
            copied = [dict(item) for item in messages]
            addition = work_mode_instruction()
            saved = _LAST_SAVED_PLAN.get()
            if saved:
                addition += (
                    f"\nA work plan was saved as {saved['id']} with status draft. Do not claim it executed. "
                    "Tell the member it requires explicit approval and Agent work mode before execution."
                )
            copied[0]["content"] = str(copied[0].get("content") or "") + "\n\n" + addition
            messages = copied
        return original_complete(self, messages, **kwargs)

    tools.AuraToolRegistry.specs = specs
    tools.AuraToolRegistry.execute = execute
    tools._explicit_write_allowed = explicit_write_allowed
    core.AuraAgent._tool_plan = tool_plan
    core.AuraAgent.respond = respond
    core.AuraModelClient.complete = complete
    _INSTALLED = True


WORK_MODE_UI_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const req=(url,opt={})=>fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt}).then(async r=>{let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b});
  const tell=(m,b=false)=>{try{window.note?.(m,b)}catch(_){}if(!window.note)console[b?'error':'log'](m)};
  const top=document.querySelector('.top'), project=document.getElementById('project');
  let select=document.getElementById('auraWorkMode');
  async function syncMode(){if(!select||typeof current==='undefined'||!current)return;try{const d=await req(`${api}/threads/${encodeURIComponent(current)}/work-mode`);select.value=d.mode||'agent'}catch(_){select.value='agent'}}
  if(top&&project&&!select){
    select=document.createElement('select');select.id='auraWorkMode';select.className='select';select.title='Aura work mode';
    select.innerHTML='<option value="ask">◌ Ask · read only</option><option value="plan">◇ Plan · save steps</option><option value="agent">✦ Agent · execute</option>';
    top.insertBefore(select,document.getElementById('auraReasoningMode')||project);
    select.onchange=async()=>{try{const d=await req(`${api}/threads/${encodeURIComponent(current)}/work-mode`,{method:'PUT',body:JSON.stringify({mode:select.value})});tell(d.detail||'Aura work mode updated.')}catch(e){tell(e.message,true);await syncMode()}};
  }
  const foot=document.querySelector('.sideFoot');let panel;
  async function renderPlans(){if(!panel||typeof current==='undefined'||!current)return;try{const d=await req(`${api}/threads/${encodeURIComponent(current)}/work-plans`),rows=d.plans||[];panel.innerHTML=`<div style="display:flex;justify-content:space-between;gap:8px"><b>Aura work plans</b><button id="closeAuraPlans" class="mini">✕</button></div><p style="color:#a9b2c8">Plans are immutable after approval. Agent mode is required to execute.</p>${rows.length?rows.map(p=>`<div style="border:1px solid #ffffff18;border-radius:12px;padding:10px;margin:8px 0"><b>${esc(p.objective)}</b><div style="color:#a9b2c8;font-size:.78rem">${esc(p.status)} · ${p.calls?.length||0} step(s)</div><div style="margin-top:7px;display:flex;gap:5px;flex-wrap:wrap">${['draft','failed'].includes(p.status)?`<button class="mini" data-plan-approve="${esc(p.id)}">Approve</button>`:''}${p.status==='approved'?`<button class="mini" data-plan-run="${esc(p.id)}">Execute</button>`:''}${['draft','approved'].includes(p.status)?`<button class="mini" data-plan-cancel="${esc(p.id)}">Cancel</button>`:''}</div>${p.error?`<div style="color:#ff9caf;margin-top:6px">${esc(p.error)}</div>`:''}</div>`).join(''):'<p style="color:#a9b2c8">No saved plans in this conversation yet.</p>'}`;document.getElementById('closeAuraPlans').onclick=()=>panel.style.display='none'}catch(e){panel.innerHTML=`<b>Aura work plans</b><p style="color:#ff9caf">${esc(e.message)}</p>`}}
  if(foot&&!document.getElementById('auraWorkPlans')){
    const btn=document.createElement('button');btn.id='auraWorkPlans';btn.className='btn';btn.textContent='◇ Work plans';foot.prepend(btn);
    panel=document.createElement('div');panel.style.cssText='position:fixed;right:14px;top:78px;bottom:14px;width:min(500px,calc(100vw - 28px));z-index:97;background:#080c18fb;border:1px solid #ffffff20;border-radius:16px;padding:16px;overflow:auto;display:none;box-shadow:0 20px 70px #000b';document.body.append(panel);
    btn.onclick=async()=>{panel.style.display=panel.style.display==='block'?'none':'block';if(panel.style.display==='block')await renderPlans()};
    panel.addEventListener('click',async e=>{const a=e.target.closest('[data-plan-approve]'),r=e.target.closest('[data-plan-run]'),c=e.target.closest('[data-plan-cancel]');const id=a?.dataset.planApprove||r?.dataset.planRun||c?.dataset.planCancel;if(!id)return;try{if(a){if(!confirm('Approve this exact Aura work plan? It still will not execute until Agent mode and Execute are selected.'))return;await req(`${api}/threads/${encodeURIComponent(current)}/work-plans/${encodeURIComponent(id)}/approve`,{method:'POST'})}if(r){if(!confirm('Execute this approved plan now through Aura Agent?'))return;const out=await req(`${api}/threads/${encodeURIComponent(current)}/work-plans/${encodeURIComponent(id)}/execute`,{method:'POST'});tell(out.completed?'Aura completed the approved work plan.':'Aura stopped because a plan step failed.',!out.completed)}if(c)await req(`${api}/threads/${encodeURIComponent(current)}/work-plans/${encodeURIComponent(id)}/cancel`,{method:'POST'});await renderPlans()}catch(err){tell(err.message,true)}});
  }
  if(typeof openThread==='function'){const prior=openThread;openThread=async function(id){const out=await prior(id);await syncMode();return out}}
  setTimeout(syncMode,450);
})();
"""


__all__ = [
    "router",
    "AuraWorkMode",
    "AuraWorkPlanStore",
    "plans",
    "active_work_mode",
    "activate_work_mode_scope",
    "detect_work_mode_command",
    "get_work_mode",
    "set_work_mode",
    "work_mode_instruction",
    "work_mode_scope",
    "install_aura_work_modes",
    "WORK_MODE_UI_SCRIPT",
]
