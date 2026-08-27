from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .esp_agent_health import router as agent_health_router
from .esp_agent_operations import router as agent_operations_router
from .esp_agent_roster import router as agent_roster_router
from .esp_brand_opportunities import router as brand_opportunities_router
from .esp_creator_discovery import router as creator_discovery_router
from .esp_creator_plan_overlay import level_up_with_creator_plan as base_level_up_portal
from .esp_level_up import HUB_NAME, capabilities_for
from .esp_niche import require_esp_hub_member

router = APIRouter()
router.include_router(agent_roster_router)
router.include_router(agent_health_router)
router.include_router(agent_operations_router)
router.include_router(creator_discovery_router)
router.include_router(brand_opportunities_router)


def _capabilities_with_commercial_built(membership: dict) -> list[dict]:
    result: list[dict] = []
    for item in capabilities_for(membership):
        value = dict(item)
        if value.get("id") == "commerce-brands":
            value["status"] = "built"
        result.append(value)
    return result


@router.get("/command-center/api/level-up/capabilities")
def level_up_capabilities_with_commercial(request: Request):
    _member, membership = require_esp_hub_member(request)
    return {"hub": HUB_NAME, "capabilities": _capabilities_with_commercial_built(membership)}


@router.get("/command-center/level-up", response_class=HTMLResponse, include_in_schema=False)
def level_up_with_agent_roster(request: Request):
    """Compose private ESP commercial surfaces plus Agent OS tools over the shared Level Up Hub."""
    _member, membership = require_esp_hub_member(request)
    response = base_level_up_portal(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    marker = "<a class='btn' href='/command-center/progress'>Progress</a>"
    commercial_link = "<a class='btn primary' href='/command-center/commercial-opportunities'>Commercial Opportunities</a>"
    if marker in html and "/command-center/commercial-opportunities" not in html:
        html = html.replace(marker, marker + commercial_link, 1)

    commercial_designed = (
        "<div class='topline'><span>Commercial Growth</span><b class='planned'>Designed</b></div>"
        "<h3>Commerce, Brand &amp; Opportunity Centre</h3>"
    )
    commercial_built = (
        "<div class='topline'><span>Commercial Growth</span><b class='live'>Built</b></div>"
        "<h3>Commerce, Brand &amp; Opportunity Centre</h3>"
    )
    if commercial_designed in html:
        html = html.replace(commercial_designed, commercial_built, 1)

    if role not in {"agent", "both", "owner"}:
        return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})

    roster_link = "<a class='btn primary' href='/command-center/agent/roster'>Assigned Creator Roster</a>"
    health_link = "<a class='btn primary' href='/command-center/agent/health'>Creator Health Queue</a>"
    operations_link = "<a class='btn primary' href='/command-center/agent/operations'>Creator Success Operations</a>"
    discovery_link = "<a class='btn primary' href='/command-center/agent/discovery'>Creator Discovery</a>"
    if marker in html and "/command-center/agent/roster" not in html:
        html = html.replace(marker, marker + roster_link + health_link + operations_link + discovery_link, 1)
    else:
        if roster_link in html and "/command-center/agent/health" not in html:
            html = html.replace(roster_link, roster_link + health_link, 1)
        if health_link in html and "/command-center/agent/operations" not in html:
            html = html.replace(health_link, health_link + operations_link, 1)
        if operations_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(operations_link, operations_link + discovery_link, 1)

    roster_partial = (
        "<div class='topline'><span>Agent OS</span><b class='partial'>Partially Built</b></div>"
        "<h3>Assigned Creator Roster</h3>"
    )
    roster_built = (
        "<div class='topline'><span>Agent OS</span><b class='live'>Built</b></div>"
        "<h3>Assigned Creator Roster</h3>"
    )
    if roster_partial in html:
        html = html.replace(roster_partial, roster_built, 1)

    operations_designed = (
        "<div class='topline'><span>Agent OS</span><b class='planned'>Designed</b></div>"
        "<h3>Agent Operations &amp; Creator Success</h3>"
    )
    operations_partial = (
        "<div class='topline'><span>Agent OS</span><b class='partial'>Partially Built</b></div>"
        "<h3>Agent Operations &amp; Creator Success</h3>"
    )
    if operations_designed in html:
        html = html.replace(operations_designed, operations_partial, 1)
    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
