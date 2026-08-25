from __future__ import annotations

import json

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from . import aura_tasks
from .aura_agent_core import AuraModelClient
from .aura_workspace_briefing import build_workspace_briefing

_INSTALLED = False


def _is_briefing_request(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    return any(
        phrase in lower
        for phrase in (
            "workspace briefing",
            "workspace brief",
            "daily briefing",
            "morning briefing",
            "weekly briefing",
            "brief me on my workspace",
        )
    )


def _drive_topic(prompt: str) -> str | None:
    clean = " ".join((prompt or "").split()).strip()
    lower = clean.lower()
    if not clean or lower in {"general", "daily briefing", "workspace briefing", "morning briefing", "weekly briefing"}:
        return None
    return clean[:500]


def scheduled_workspace_briefing(task: dict) -> str:
    user_id = str(task.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("Scheduled workspace briefing is missing its user context")
    prompt = str(task.get("prompt") or "general").strip()
    hours = 168 if "week" in prompt.lower() else 24
    data = build_workspace_briefing(
        user_id,
        hours=hours,
        drive_query=_drive_topic(prompt),
        limit=8,
    )
    reply = AuraModelClient().complete(
        [
            {
                "role": "system",
                "content": (
                    "You are Aura completing a private scheduled connected-workspace briefing. "
                    "Summarize only the supplied Calendar/Gmail/Drive metadata. Do not imply that email bodies were opened, "
                    "Drive files were downloaded, emails were sent, events were created, or projects were changed. "
                    "Call out connector/service errors clearly. Keep the briefing concise and action-oriented."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"scheduled_briefing_topic": prompt, "workspace_data": data}, ensure_ascii=False)[:65000],
            },
        ],
        temperature=0.2,
    )
    return "☀ Aura workspace briefing\n\n" + reply.text.strip()


def install_aura_scheduled_briefing() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Extend the durable scheduler's accepted kinds without altering its lease/retry machinery.
    aura_tasks._TASK_KINDS.add("workspace_briefing")

    # Upgrade the public tool contract so Aura can intentionally choose this background type.
    for index, spec in enumerate(list(tools.TOOL_SPECS)):
        if spec.name != "create_aura_task":
            continue
        replacement = tools.ToolSpec(
            name=spec.name,
            description=(
                "Create a durable read-only background Aura task. Supports reminders, prompt follow-ups, web research "
                "and connected Google workspace briefings. It cannot modify projects, send email, create calendar events, "
                "publish social content, run code or use voice cloning."
            ),
            arguments={
                "title": "Short task title.",
                "kind": "reminder|prompt|research|workspace_briefing",
                "prompt": "Task instruction. For workspace_briefing use 'general' or an optional Drive topic/project query.",
                "delay_minutes": "Relative delay, optional.",
                "run_at": "Timezone-aware ISO timestamp, optional alternative to delay_minutes.",
                "interval_minutes": "Optional recurrence; minimum 60 minutes.",
            },
            write=True,
        )
        tools.TOOL_SPECS[index] = replacement
        tools._SPEC_BY_NAME[replacement.name] = replacement
        break

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name == "create_aura_task" and _is_briefing_request(latest_user_message):
            args = dict(call.arguments or {})
            args["kind"] = "workspace_briefing"
            if not str(args.get("prompt") or "").strip():
                args["prompt"] = "general"
            call = tools.ToolCall(name=call.name, arguments=args)
        return original_execute(self, call, latest_user_message=latest_user_message)

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        if _is_briefing_request(text):
            delay = aura_tasks._relative_minutes(text)
            if delay is not None:
                return tools.ToolPlan(
                    calls=[
                        tools.ToolCall(
                            name="create_aura_task",
                            arguments={
                                "title": "Aura Workspace Briefing",
                                "kind": "workspace_briefing",
                                "prompt": pinned_project or "general",
                                "delay_minutes": delay,
                            },
                        )
                    ]
                )
        return original_direct(text, pinned_project, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    _INSTALLED = True


__all__ = [
    "install_aura_scheduled_briefing",
    "scheduled_workspace_briefing",
    "_is_briefing_request",
    "_drive_topic",
]
