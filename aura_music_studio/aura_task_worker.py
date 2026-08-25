from __future__ import annotations

import json
import os
import signal
import time
from uuid import uuid4

from .aura_agent_core import AuraModelClient
from .aura_notifications import notification_store
from .aura_productivity_tools import _source_wrap, source_markdown
from .aura_scheduled_briefing import install_aura_scheduled_briefing, scheduled_workspace_briefing
from .aura_tasks import task_store
from .web_access import AuraWebGateway

_RUNNING = True

# The task worker imports the same scheduled-briefing extension as the web process so
# validation/accepted task kinds remain consistent. This adds only read-only connector work.
install_aura_scheduled_briefing()


def _stop(*_args):
    global _RUNNING
    _RUNNING = False


def _research(prompt: str) -> str:
    gateway = AuraWebGateway()
    diagnostics = gateway.diagnostics()
    if not diagnostics.get("enabled") or not diagnostics.get("self_hosted_search_configured"):
        raise RuntimeError("Aura Task research requires AURA_WEB_ENABLED and a configured AURA_SEARXNG_URL")
    raw = gateway.search(prompt, limit=8)
    rows, _ = _source_wrap(raw, start=0)
    reply = AuraModelClient().complete(
        [
            {
                "role": "system",
                "content": (
                    "You are Aura completing a scheduled read-only research task. Summarize only the supplied web results. "
                    "Use [S1], [S2] source ids for factual claims. Do not claim any project/social/code action happened."
                ),
            },
            {"role": "user", "content": json.dumps({"scheduled_task": prompt, "sources": rows}, ensure_ascii=False)[:65000]},
        ],
        temperature=0.25,
    )
    tool_results = [{"tool": "web_search", "ok": True, "result": rows}]
    block = source_markdown(tool_results)
    return reply.text.rstrip() + ("\n\n" + block if block else "")


def _prompt(prompt: str) -> str:
    reply = AuraModelClient().complete(
        [
            {
                "role": "system",
                "content": (
                    "You are Aura completing a scheduled read-only follow-up. Answer the scheduled prompt usefully, but do not "
                    "claim any web lookup, project change, social publication, code execution, voice cloning or external action."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.35,
    )
    return reply.text


def run_task(task: dict) -> str:
    kind = str(task.get("kind") or "reminder")
    prompt = str(task.get("prompt") or "").strip()
    if kind == "reminder":
        return "⏰ Aura reminder: " + prompt
    if kind == "research":
        return "🔎 Scheduled Aura research\n\n" + _research(prompt)
    if kind == "prompt":
        return "✦ Scheduled Aura follow-up\n\n" + _prompt(prompt)
    if kind == "workspace_briefing":
        return scheduled_workspace_briefing(task)
    raise ValueError(f"Unsupported Aura Task kind: {kind}")


def _notify_success(task: dict, result: str, message_id: str | None) -> None:
    notification_store.create(
        task["user_id"],
        thread_id=task["thread_id"],
        kind="aura_task",
        title=f"Aura Task complete · {task.get('title') or 'Scheduled task'}",
        body=result[:1800],
        resource_kind="aura_message",
        resource_id=message_id,
    )


def _notify_final_failure(task: dict, error: str) -> None:
    notification_store.create(
        task["user_id"],
        thread_id=task["thread_id"],
        kind="aura_task_error",
        title=f"Aura Task needs attention · {task.get('title') or 'Scheduled task'}",
        body=("The scheduled task stopped after repeated failures. " + error)[:1800],
        resource_kind="aura_task",
        resource_id=task["id"],
    )


def run_worker() -> None:
    global _RUNNING
    _RUNNING = True
    worker_id = os.getenv("AURA_TASK_WORKER_ID", "").strip() or f"aura-task-{uuid4().hex[:10]}"
    poll = max(1.0, min(float(os.getenv("AURA_TASK_POLL_SECONDS", "5")), 60.0))
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while _RUNNING:
        task_store.heartbeat(
            worker_id,
            {
                "poll_seconds": poll,
                "background_project_writes_allowed": False,
                "background_connector_reads_allowed": True,
                "background_connector_writes_allowed": False,
            },
        )
        task = task_store.claim_due(worker_id)
        if task is None:
            time.sleep(poll)
            continue
        error = None
        try:
            result = run_task(task)
            # Durable result delivery uses the originating private Aura thread. The worker
            # writes only assistant prose; it does not instantiate AuraToolRegistry.
            message = task_store.chat_store.add_message(task["user_id"], task["thread_id"], "assistant", result[:100000])
            _notify_success(task, result, str(message.get("id") or "") or None)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        task_store.finish(task, error=error)
        if error:
            state = task_store.get(task["user_id"], task["id"])
            if state and state.get("status") == "failed":
                try:
                    _notify_final_failure(task, error)
                except Exception:
                    pass


if __name__ == "__main__":
    run_worker()
