from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_chat_store import AuraChatStore
from .aura_runtime_context import current_turn

router = APIRouter(tags=["Aura Tasks"])
store = AuraChatStore()
_INSTALLED = False
_TASK_KINDS = {"reminder", "prompt", "research"}
_MIN_INTERVAL = 3600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    clean = (value or "").strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    result = datetime.fromisoformat(clean)
    if result.tzinfo is None:
        raise ValueError("Task run_at must include a timezone/UTC offset")
    return result.astimezone(timezone.utc)


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    kind: str = Field(default="reminder", max_length=20)
    prompt: str = Field(min_length=1, max_length=10000)
    run_at: str | None = Field(default=None, max_length=80)
    delay_minutes: int | None = Field(default=None, ge=1, le=525600)
    interval_minutes: int | None = Field(default=None, ge=60, le=525600)

    @model_validator(mode="after")
    def timing(self):
        if bool(self.run_at) == bool(self.delay_minutes):
            raise ValueError("Provide exactly one of run_at or delay_minutes")
        if self.kind not in _TASK_KINDS:
            raise ValueError("Task kind must be reminder, prompt or research")
        return self


class TaskPatchRequest(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=140)


class AuraTaskStore:
    def __init__(self, chat_store: AuraChatStore | None = None):
        self.chat_store = chat_store or store
        self.ensure_schema()

    def ensure_schema(self):
        with self.chat_store._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_tasks (
                       id TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       thread_id TEXT NOT NULL,
                       title TEXT NOT NULL,
                       kind TEXT NOT NULL,
                       prompt TEXT NOT NULL,
                       next_run_at TEXT NOT NULL,
                       interval_seconds INTEGER,
                       status TEXT NOT NULL DEFAULT 'active',
                       enabled INTEGER NOT NULL DEFAULT 1,
                       lease_token TEXT,
                       lease_until TEXT,
                       last_run_at TEXT,
                       last_error TEXT,
                       failure_count INTEGER NOT NULL DEFAULT 0,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_aura_tasks_due ON aura_tasks(enabled,status,next_run_at,lease_until)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_aura_tasks_user ON aura_tasks(user_id,created_at DESC)")
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_task_runtime (
                       worker_id TEXT PRIMARY KEY,
                       heartbeat_at TEXT NOT NULL,
                       detail_json TEXT NOT NULL DEFAULT '{}'
                   )"""
            )

    @staticmethod
    def _public(row) -> dict:
        return {
            "id": row["id"], "thread_id": row["thread_id"], "title": row["title"], "kind": row["kind"],
            "prompt": row["prompt"], "next_run_at": row["next_run_at"],
            "interval_minutes": (int(row["interval_seconds"]) // 60) if row["interval_seconds"] else None,
            "status": row["status"], "enabled": bool(row["enabled"]), "last_run_at": row["last_run_at"],
            "last_error": row["last_error"], "failure_count": int(row["failure_count"] or 0),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "background_project_writes_allowed": False,
        }

    def create(self, user_id: str, thread_id: str, body: TaskCreateRequest) -> dict:
        if not self.chat_store.thread(user_id, thread_id):
            raise KeyError(thread_id)
        run = parse_iso(body.run_at) if body.run_at else utcnow() + timedelta(minutes=int(body.delay_minutes or 1))
        if run < utcnow() - timedelta(seconds=5):
            raise ValueError("Task run time is in the past")
        interval = int(body.interval_minutes * 60) if body.interval_minutes else None
        if interval is not None and interval < _MIN_INTERVAL:
            raise ValueError("Recurring Aura Tasks cannot run more frequently than once per hour")
        task_id, now = uuid4().hex, iso(utcnow())
        with self.chat_store._connect() as con:
            con.execute(
                """INSERT INTO aura_tasks(id,user_id,thread_id,title,kind,prompt,next_run_at,interval_seconds,status,enabled,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, user_id, thread_id, " ".join(body.title.split())[:140], body.kind, body.prompt.strip(), iso(run), interval, "active", 1, now, now),
            )
        return self.get(user_id, task_id) or {}

    def get(self, user_id: str, task_id: str) -> dict | None:
        with self.chat_store._connect() as con:
            row = con.execute("SELECT * FROM aura_tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        return self._public(row) if row else None

    def list(self, user_id: str, limit: int = 100) -> list[dict]:
        with self.chat_store._connect() as con:
            rows = con.execute("SELECT * FROM aura_tasks WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, max(1, min(int(limit), 200)))).fetchall()
        return [self._public(row) for row in rows]

    def patch(self, user_id: str, task_id: str, body: TaskPatchRequest) -> dict:
        current = self.get(user_id, task_id)
        if not current:
            raise KeyError(task_id)
        title = current["title"] if body.title is None else " ".join(body.title.split())[:140]
        enabled = current["enabled"] if body.enabled is None else bool(body.enabled)
        with self.chat_store._connect() as con:
            con.execute(
                "UPDATE aura_tasks SET title=?,enabled=?,updated_at=? WHERE id=? AND user_id=?",
                (title, 1 if enabled else 0, iso(utcnow()), task_id, user_id),
            )
        return self.get(user_id, task_id) or {}

    def delete(self, user_id: str, task_id: str) -> bool:
        with self.chat_store._connect() as con:
            cur = con.execute("DELETE FROM aura_tasks WHERE id=? AND user_id=?", (task_id, user_id))
        return cur.rowcount > 0

    def heartbeat(self, worker_id: str, detail: dict | None = None):
        with self.chat_store._connect() as con:
            con.execute(
                """INSERT INTO aura_task_runtime(worker_id,heartbeat_at,detail_json) VALUES (?,?,?)
                   ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,detail_json=excluded.detail_json""",
                (worker_id, iso(utcnow()), json.dumps(detail or {}, ensure_ascii=False, default=str)[:10000]),
            )

    def worker_status(self, freshness_seconds: int = 120) -> dict:
        cutoff = utcnow() - timedelta(seconds=freshness_seconds)
        with self.chat_store._connect() as con:
            rows = con.execute("SELECT worker_id,heartbeat_at,detail_json FROM aura_task_runtime ORDER BY heartbeat_at DESC LIMIT 10").fetchall()
        public = []
        for row in rows:
            try:
                heartbeat = parse_iso(row["heartbeat_at"])
            except Exception:
                continue
            public.append({"worker_id": row["worker_id"], "heartbeat_at": row["heartbeat_at"], "fresh": heartbeat >= cutoff})
        return {"ready": any(row["fresh"] for row in public), "workers": public}

    def claim_due(self, worker_id: str, *, lease_seconds: int = 180) -> dict | None:
        now, lease_until, token = utcnow(), utcnow() + timedelta(seconds=lease_seconds), uuid4().hex
        con = self.chat_store._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT * FROM aura_tasks
                   WHERE enabled=1 AND status='active' AND next_run_at<=?
                     AND (lease_until IS NULL OR lease_until<?)
                   ORDER BY next_run_at ASC LIMIT 1""",
                (iso(now), iso(now)),
            ).fetchone()
            if not row:
                con.commit()
                return None
            con.execute(
                "UPDATE aura_tasks SET lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                (token, iso(lease_until), iso(now), row["id"]),
            )
            con.commit()
            result = dict(row)
            result["lease_token"] = token
            return result
        finally:
            con.close()

    def finish(self, task: dict, *, error: str | None = None):
        now = utcnow()
        interval = task.get("interval_seconds")
        failures = int(task.get("failure_count") or 0)
        if error:
            failures += 1
            if failures >= 3:
                status, enabled, next_run = "failed", 0, task["next_run_at"]
            else:
                status, enabled, next_run = "active", 1, iso(now + timedelta(minutes=5))
        elif interval:
            status, enabled = "active", 1
            next_run_dt = parse_iso(task["next_run_at"])
            while next_run_dt <= now:
                next_run_dt += timedelta(seconds=int(interval))
            next_run = iso(next_run_dt)
            failures = 0
        else:
            status, enabled, next_run, failures = "completed", 0, task["next_run_at"], 0
        with self.chat_store._connect() as con:
            con.execute(
                """UPDATE aura_tasks SET next_run_at=?,status=?,enabled=?,lease_token=NULL,lease_until=NULL,last_run_at=?,last_error=?,failure_count=?,updated_at=?
                   WHERE id=? AND lease_token=?""",
                (next_run, status, enabled, iso(now), error[:2000] if error else None, failures, iso(now), task["id"], task["lease_token"]),
            )


task_store = AuraTaskStore(store)

TASK_SPECS = [
    tools.ToolSpec(
        name="list_aura_tasks",
        description="List this member's durable Aura reminder/research/prompt tasks and whether they are active.",
        arguments={},
    ),
    tools.ToolSpec(
        name="create_aura_task",
        description="Create a durable read-only background Aura task. Supports reminders, general prompt follow-ups and web research. It cannot modify projects, publish social content, run code or use voice cloning.",
        arguments={"title":"Short task title.","kind":"reminder|prompt|research","prompt":"Reminder/research/prompt instruction.","delay_minutes":"Relative delay, optional.","run_at":"Timezone-aware ISO timestamp, optional alternative to delay_minutes.","interval_minutes":"Optional recurrence; minimum 60 minutes."},
        write=True,
    ),
]


def _task_requested(text: str) -> bool:
    lower = (text or "").lower()
    return any(word in lower for word in ("remind me", "schedule", "every hour", "every day", "every week", "later", "in an hour", "in a minute", "task"))


def _relative_minutes(text: str) -> int | None:
    lower = (text or "").lower()
    match = re.search(r"\bin\s+(\d+)\s*(minute|minutes|hour|hours|day|days)\b", lower)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    return value * (1440 if unit.startswith("day") else 60 if unit.startswith("hour") else 1)


def install_aura_task_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for spec in TASK_SPECS:
        if spec.name not in {item.name for item in tools.TOOL_SPECS}:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec
    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name not in {"list_aura_tasks", "create_aura_task"}:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        if call.name == "list_aura_tasks":
            return {"tasks": task_store.list(self.member.user_id), "worker": task_store.worker_status(), "background_project_writes_allowed": False}
        if not _task_requested(latest_user_message):
            raise PermissionError("Creating an Aura Task requires an explicit reminder/schedule/task instruction")
        turn = current_turn()
        if turn is None or turn.user_id != self.member.user_id:
            raise RuntimeError("Current Aura conversation context is unavailable")
        args = dict(call.arguments or {})
        if not args.get("run_at") and not args.get("delay_minutes"):
            args["delay_minutes"] = _relative_minutes(latest_user_message)
        if not args.get("run_at") and not args.get("delay_minutes"):
            raise ValueError("Aura needs an exact timezone-aware run time or a relative delay such as 'in 2 hours'")
        body = TaskCreateRequest(
            title=str(args.get("title") or "Aura Task"),
            kind=str(args.get("kind") or "reminder"),
            prompt=str(args.get("prompt") or latest_user_message),
            run_at=args.get("run_at"),
            delay_minutes=args.get("delay_minutes"),
            interval_minutes=args.get("interval_minutes"),
        )
        return {"task": task_store.create(self.member.user_id, turn.thread_id, body), "worker": task_store.worker_status(), "background_project_writes_allowed": False}

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        prior = original_direct(text, pinned_project, web_enabled)
        if prior is not None:
            return prior
        delay = _relative_minutes(text)
        if delay is not None and _task_requested(text):
            return tools.ToolPlan(calls=[tools.ToolCall(name="create_aura_task", arguments={"title":"Aura Reminder","kind":"reminder","prompt":text,"delay_minutes":delay})])
        return None

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and _task_requested(text):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


@router.get("/aura-intelligence/api/tasks")
def list_tasks(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return {"tasks": task_store.list(member.user_id), "worker": task_store.worker_status()}


@router.post("/aura-intelligence/api/threads/{thread_id}/tasks")
def create_task(thread_id: str, body: TaskCreateRequest, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    try:
        return task_store.create(member.user_id, thread_id, body)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/aura-intelligence/api/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatchRequest, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    try:
        return task_store.patch(member.user_id, task_id, body)
    except KeyError as exc:
        raise HTTPException(404, "Aura Task not found") from exc


@router.delete("/aura-intelligence/api/tasks/{task_id}")
def delete_task(task_id: str, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not task_store.delete(member.user_id, task_id):
        raise HTTPException(404, "Aura Task not found")
    return {"deleted": True, "task_id": task_id}


__all__ = ["router", "AuraTaskStore", "task_store", "install_aura_task_tools", "TaskCreateRequest"]
