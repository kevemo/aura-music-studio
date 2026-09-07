from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .esp_agent_development_planner import router as development_planner_router
from .esp_agent_health import router as agent_health_router
from .esp_agent_operations import router as agent_operations_router
from .esp_agent_performance_compensation import router as performance_compensation_router
from .esp_agent_recruitment_academy import router as recruitment_academy_router
from .esp_agent_report_exports import router as report_exports_router
from .esp_agent_roster import router as agent_roster_router
from .esp_backstage_evidence import router as backstage_evidence_router
from .esp_backstage_vision_portal import router as backstage_vision_router
from .esp_brand_opportunities import router as brand_opportunities_router
from .esp_creator_discovery import router as creator_discovery_router
from .esp_creator_plan_overlay import level_up_with_creator_plan as base_level_up_portal
from .esp_level_up import HUB_NAME, capabilities_for
from .esp_niche import require_esp_hub_member
from .esp_support_escalation import router as support_escalation_router

router = APIRouter()
router.include_router(agent_roster_router)
router.include_router(agent_health_router)
router.include_router(agent_operations_router)
router.include_router(support_escalation_router)
router.include_router(backstage_evidence_router)
router.include_router(backstage_vision_router)
router.include_router(development_planner_router)
router.include_router(report_exports_router)
router.include_router(performance_compensation_router)
router.include_router(recruitment_academy_router)
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
    support_link = "<a class='btn primary' href='/command-center/agent/support'>Support &amp; Escalation Desk</a>"
    operations_link = "<a class='btn primary' href='/command-center/agent/operations'>Creator Success Operations</a>"
    backstage_link = "<a class='btn primary' href='/command-center/agent/backstage-evidence'>Backstage Evidence &amp; Analysis</a>"
    vision_link = "<a class='btn primary' href='/command-center/agent/backstage-vision'>Screenshot Data Review</a>"
    development_link = "<a class='btn primary' href='/command-center/agent/development'>Creator Development Planner</a>"
    report_link = "<a class='btn primary' href='/command-center/agent/reports'>Creator Reports &amp; Exports</a>"
    performance_link = "<a class='btn primary' href='/command-center/agent/performance'>Agent Performance &amp; Compensation</a>"
    academy_link = "<a class='btn primary' href='/command-center/agent/recruitment-academy'>Recruitment Academy</a>"
    discovery_link = "<a class='btn primary' href='/command-center/agent/discovery'>Creator Discovery</a>"
    crm_link = "<a class='btn primary' href='/command-center/agent/leads'>Recruitment Lead CRM</a>"
    if marker in html and "/command-center/agent/roster" not in html:
        html = html.replace(
            marker,
            marker + roster_link + health_link + support_link + operations_link + backstage_link + vision_link + development_link + report_link + performance_link + academy_link + discovery_link + crm_link,
            1,
        )
    else:
        if roster_link in html and "/command-center/agent/health" not in html:
            html = html.replace(roster_link, roster_link + health_link, 1)
        if health_link in html and "/command-center/agent/support" not in html:
            html = html.replace(health_link, health_link + support_link, 1)
        if support_link in html and "/command-center/agent/operations" not in html:
            html = html.replace(support_link, support_link + operations_link, 1)
        elif health_link in html and "/command-center/agent/operations" not in html:
            html = html.replace(health_link, health_link + operations_link, 1)
        if operations_link in html and "/command-center/agent/backstage-evidence" not in html:
            html = html.replace(operations_link, operations_link + backstage_link, 1)
        if backstage_link in html and "/command-center/agent/backstage-vision" not in html:
            html = html.replace(backstage_link, backstage_link + vision_link, 1)
        if vision_link in html and "/command-center/agent/development" not in html:
            html = html.replace(vision_link, vision_link + development_link, 1)
        elif backstage_link in html and "/command-center/agent/development" not in html:
            html = html.replace(backstage_link, backstage_link + development_link, 1)
        if development_link in html and "/command-center/agent/reports" not in html:
            html = html.replace(development_link, development_link + report_link, 1)
        if report_link in html and "/command-center/agent/performance" not in html:
            html = html.replace(report_link, report_link + performance_link, 1)
        if performance_link in html and "/command-center/agent/recruitment-academy" not in html:
            html = html.replace(performance_link, performance_link + academy_link, 1)
        elif report_link in html and "/command-center/agent/recruitment-academy" not in html:
            html = html.replace(report_link, report_link + academy_link, 1)
        elif development_link in html and "/command-center/agent/recruitment-academy" not in html:
            html = html.replace(development_link, development_link + academy_link, 1)
        if academy_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(academy_link, academy_link + discovery_link, 1)
        elif performance_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(performance_link, performance_link + discovery_link, 1)
        elif report_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(report_link, report_link + discovery_link, 1)
        elif development_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(development_link, development_link + discovery_link, 1)
        elif backstage_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(backstage_link, backstage_link + discovery_link, 1)
        elif operations_link in html and "/command-center/agent/discovery" not in html:
            html = html.replace(operations_link, operations_link + discovery_link, 1)
        if discovery_link in html and "/command-center/agent/leads" not in html:
            html = html.replace(discovery_link, discovery_link + crm_link, 1)
        elif academy_link in html and "/command-center/agent/leads" not in html:
            html = html.replace(academy_link, academy_link + crm_link, 1)

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
