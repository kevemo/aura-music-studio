from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .esp_agent_roster import router as agent_roster_router
from .esp_creator_plan_overlay import level_up_with_creator_plan as base_level_up_portal
from .esp_niche import require_esp_hub_member

router = APIRouter()
router.include_router(agent_roster_router)


@router.get("/command-center/level-up", response_class=HTMLResponse, include_in_schema=False)
def level_up_with_agent_roster(request: Request):
    member, membership = require_esp_hub_member(request)
    response = base_level_up_portal(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    marker = "<a class='btn' href='/command-center/progress'>Progress</a>"
    link = "<a class='btn primary' href='/command-center/agent/roster'>Assigned Creator Roster</a>"
    if marker in html and "/command-center/agent/roster" not in html:
        html = html.replace(marker, marker + link, 1)

    partial = (
        "<div class='topline'><span>Agent OS</span><b class='partial'>Partially Built</b></div>"
        "<h3>Assigned Creator Roster</h3>"
    )
    built = (
        "<div class='topline'><span>Agent OS</span><b class='live'>Built</b></div>"
        "<h3>Assigned Creator Roster</h3>"
    )
    if partial in html:
        html = html.replace(partial, built, 1)
    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
