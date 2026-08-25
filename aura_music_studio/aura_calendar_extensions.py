from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_connectors import GoogleConnector, google

router = APIRouter(tags=["Aura Calendar"])
_INSTALLED = False

CALENDAR_EVENT_SPEC = tools.ToolSpec(
    "google_calendar_event_read",
    "Read one explicitly selected event from the member's connected primary Google Calendar using its stable event id. Returns bounded attendee, description, location and conference metadata. Read-only; it never modifies the event.",
    {"event_id": "Stable Google Calendar event id returned by google_calendar_events or Aura Today."},
)


def _clean_event_id(value: str) -> str:
    event_id = str(value or "").strip()
    if not event_id:
        raise ValueError("Calendar event id is required")
    if len(event_id) > 1024:
        raise ValueError("Calendar event id is too long")
    if any(ord(ch) < 32 for ch in event_id):
        raise ValueError("Calendar event id contains invalid control characters")
    return event_id


def read_calendar_event(user_id: str, event_id: str, *, connector: GoogleConnector | None = None) -> dict:
    selected = _clean_event_id(event_id)
    service = connector or google
    data = service._get(
        user_id,
        "calendar",
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{quote(selected, safe='')}",
        params={
            "maxAttendees": 100,
            "fields": (
                "id,summary,description,location,start,end,status,htmlLink,hangoutLink,"
                "organizer,creator,attendees,conferenceData,attachments,eventType,"
                "transparency,visibility,recurringEventId"
            ),
        },
    )

    attendees = []
    for row in (data.get("attendees") or [])[:100]:
        attendees.append(
            {
                "email": str(row.get("email") or "")[:320] or None,
                "display_name": str(row.get("displayName") or "")[:300] or None,
                "response_status": str(row.get("responseStatus") or "")[:80] or None,
                "self": bool(row.get("self")),
                "organizer": bool(row.get("organizer")),
                "optional": bool(row.get("optional")),
            }
        )

    entry_points = []
    conference = data.get("conferenceData") or {}
    for row in (conference.get("entryPoints") or [])[:12]:
        entry_points.append(
            {
                "type": str(row.get("entryPointType") or "")[:80] or None,
                "uri": str(row.get("uri") or "")[:2000] or None,
                "label": str(row.get("label") or "")[:300] or None,
            }
        )

    attachments = []
    for row in (data.get("attachments") or [])[:20]:
        attachments.append(
            {
                "title": str(row.get("title") or "")[:500] or None,
                "mime_type": str(row.get("mimeType") or "")[:200] or None,
                "file_id": str(row.get("fileId") or "")[:1024] or None,
                "file_url": str(row.get("fileUrl") or "")[:3000] or None,
            }
        )

    def identity(row):
        row = row or {}
        return {
            "email": str(row.get("email") or "")[:320] or None,
            "display_name": str(row.get("displayName") or "")[:300] or None,
            "self": bool(row.get("self")),
        }

    return {
        "id": data.get("id") or selected,
        "summary": str(data.get("summary") or "(untitled)")[:1000],
        "description": str(data.get("description") or "")[:12000],
        "location": str(data.get("location") or "")[:2000] or None,
        "start": data.get("start") or {},
        "end": data.get("end") or {},
        "status": data.get("status"),
        "html_link": data.get("htmlLink"),
        "hangout_link": data.get("hangoutLink"),
        "organizer": identity(data.get("organizer")),
        "creator": identity(data.get("creator")),
        "attendees": attendees,
        "conference": {
            "solution_name": str(((conference.get("conferenceSolution") or {}).get("name")) or "")[:300] or None,
            "entry_points": entry_points,
        },
        "attachments": attachments,
        "event_type": data.get("eventType"),
        "transparency": data.get("transparency"),
        "visibility": data.get("visibility"),
        "recurring_event_id": data.get("recurringEventId"),
        "read_only": True,
        "event_modified": False,
        "attachments_downloaded": False,
        "private_extended_properties_included": False,
        "tokens_exposed": False,
    }


def _calendar_detail_related(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    return any(
        phrase in lower
        for phrase in (
            "calendar event id",
            "calendar event with id",
            "read this calendar event",
            "read the calendar event",
            "meeting details",
            "prepare me for this meeting",
            "prepare me for the meeting",
        )
    )


def install_aura_calendar_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if CALENDAR_EVENT_SPEC.name not in {item.name for item in tools.TOOL_SPECS}:
        tools.TOOL_SPECS.append(CALENDAR_EVENT_SPEC)
        tools._SPEC_BY_NAME[CALENDAR_EVENT_SPEC.name] = CALENDAR_EVENT_SPEC

    original_execute = tools.AuraToolRegistry.execute
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name != "google_calendar_event_read":
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        return read_calendar_event(self.member.user_id, str((call.arguments or {}).get("event_id") or ""))

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and _calendar_detail_related(text):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


@router.get("/aura-intelligence/api/connectors/google/calendar/events/{event_id:path}")
def calendar_event_detail(event_id: str, request: Request):
    member = _member(request)
    try:
        return read_calendar_event(member.user_id, event_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


__all__ = ["router", "read_calendar_event", "install_aura_calendar_extensions", "CALENDAR_EVENT_SPEC"]
