from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_connectors import google, vault

_INSTALLED = False

BRIEF_SPEC = tools.ToolSpec(
    "google_workspace_briefing",
    "Create a bounded read-only briefing from the member's explicitly connected Google services: upcoming Calendar events, recent/unread Gmail metadata/snippets, and optional Drive search metadata. It does not open email bodies, download Drive files, send mail or create events.",
    {
        "hours": "Upcoming calendar horizon from now, 1-168 hours; default 24.",
        "gmail_query": "Optional Gmail search query; default is: is:unread newer_than:7d.",
        "drive_query": "Optional Drive topic/project query. Drive is not searched when omitted.",
        "limit": "Per-service result cap, 1-15; default 8.",
    },
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connected_services(user_id: str) -> set[str]:
    rows = vault.status(user_id)
    for row in rows:
        if row.get("provider") == "google":
            return {str(item).lower() for item in (row.get("services") or [])}
    return set()


def build_workspace_briefing(
    user_id: str,
    *,
    hours: int = 24,
    gmail_query: str | None = None,
    drive_query: str | None = None,
    limit: int = 8,
) -> dict:
    horizon = max(1, min(int(hours or 24), 168))
    cap = max(1, min(int(limit or 8), 15))
    services = _connected_services(user_id)
    if not services:
        raise PermissionError("Connect at least one Google service to Aura before requesting a workspace briefing")

    start = _now()
    end = start + timedelta(hours=horizon)
    result: dict = {
        "generated_at": start.isoformat(),
        "calendar_horizon_hours": horizon,
        "connected_services": sorted(services),
        "read_only": True,
        "tokens_exposed": False,
        "email_bodies_opened": False,
        "drive_files_downloaded": False,
        "calendar": {"available": "calendar" in services, "events": []},
        "gmail": {"available": "gmail" in services, "messages": []},
        "drive": {"available": "drive" in services, "searched": False, "files": []},
        "service_errors": {},
    }

    if "calendar" in services:
        try:
            data = google.calendar_events(
                user_id,
                time_min=start.isoformat(),
                time_max=end.isoformat(),
                limit=cap,
            )
            result["calendar"] = {
                "available": True,
                "time_min": data.get("time_min"),
                "time_max": data.get("time_max"),
                "events": (data.get("events") or [])[:cap],
            }
        except Exception as exc:
            result["service_errors"]["calendar"] = str(exc)[:500]

    if "gmail" in services:
        query = " ".join((gmail_query or "is:unread newer_than:7d").split())[:1000]
        try:
            data = google.gmail_search(user_id, query, cap)
            result["gmail"] = {
                "available": True,
                "query": query,
                "messages": (data.get("messages") or [])[:cap],
                "message_bodies_included": False,
            }
        except Exception as exc:
            result["service_errors"]["gmail"] = str(exc)[:500]

    clean_drive_query = " ".join((drive_query or "").split())[:500]
    if "drive" in services and clean_drive_query:
        try:
            data = google.drive_search(user_id, clean_drive_query, cap)
            result["drive"] = {
                "available": True,
                "searched": True,
                "query": clean_drive_query,
                "files": (data.get("files") or [])[:cap],
            }
        except Exception as exc:
            result["service_errors"]["drive"] = str(exc)[:500]
    elif "drive" in services:
        result["drive"]["reason_not_searched"] = "No Drive topic/project query was supplied. Aura does not bulk-scan Drive for a general briefing."

    return result


def _wants_briefing(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    phrases = (
        "workspace briefing",
        "workspace brief",
        "google briefing",
        "brief me on my workspace",
        "brief me on my google",
        "what needs my attention today",
        "what needs my attention in my workspace",
        "give me my daily briefing",
        "give me a daily briefing",
        "give me my morning briefing",
    )
    return any(phrase in lower for phrase in phrases)


def install_aura_workspace_briefing() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if BRIEF_SPEC.name not in {item.name for item in tools.TOOL_SPECS}:
        tools.TOOL_SPECS.append(BRIEF_SPEC)
        tools._SPEC_BY_NAME[BRIEF_SPEC.name] = BRIEF_SPEC

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name != "google_workspace_briefing":
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        args = dict(call.arguments or {})
        return build_workspace_briefing(
            self.member.user_id,
            hours=int(args.get("hours") or 24),
            gmail_query=str(args.get("gmail_query") or "") or None,
            drive_query=str(args.get("drive_query") or "") or None,
            limit=int(args.get("limit") or 8),
        )

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        if _wants_briefing(text):
            arguments = {"hours": 24, "limit": 8}
            if pinned_project:
                arguments["drive_query"] = pinned_project
            return tools.ToolPlan(calls=[tools.ToolCall(name="google_workspace_briefing", arguments=arguments)])
        return original_direct(text, pinned_project, web_enabled)

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and _wants_briefing(text):
            return False
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


__all__ = [
    "install_aura_workspace_briefing",
    "build_workspace_briefing",
    "_wants_briefing",
]
