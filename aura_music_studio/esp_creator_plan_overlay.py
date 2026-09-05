from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .aura_os_contract import router as aura_os_router
from .esp_brand_revenue_os import router as brand_revenue_os_router
from .esp_broadcast_tech import router as broadcast_tech_router
from .esp_chat9_product_integration import router as chat9_product_router
from .esp_collaborations import router as collaborations_router
from .esp_commercial_growth import router as commercial_growth_router
from .esp_creator_data_import_overlay import router as creator_data_import_router
from .esp_creator_live_show_planner import router as live_show_planner_router
from .esp_creator_niche_discovery import router as niche_discovery_router
from .esp_creator_plan import router as creator_plan_router
from .esp_creator_progress_intelligence import router as progress_intelligence_router
from .esp_creator_reviews import router as creator_reviews_router
from .esp_creator_tech_vault import router as creator_tech_vault_router
from .esp_incentives import router as incentives_router
from .esp_learning_library import router as learning_library_router
from .esp_level_up import level_up_portal as base_level_up_portal
from .esp_member_hub import router as member_hub_router
from .esp_owner_focus import router as owner_focus_router
from .esp_owner_operations_intelligence import router as owner_operations_intelligence_router
from .esp_role_dashboard_switch import router as role_dashboard_router
from .esp_service_registry import router as service_registry_router
from .esp_shop_automation_overlay import router as shop_automation_router
from .esp_social_media_library import router as social_media_library_router
from .esp_support_center import router as support_center_router
from .esp_support_sla import router as support_sla_router
from .shared_sky_internal_media_api import router as shared_sky_internal_media_router
from .shared_sky_streaming_studios import router as shared_sky_router
from .shared_sky_transport_api import router as shared_sky_transport_router
from .shared_sky_worker_control import scheduler_run_due, scheduler_status
from .universal_creative_library import router as universal_creative_library_router

router = APIRouter()
router.include_router(member_hub_router)
router.include_router(learning_library_router)
router.include_router(incentives_router)
router.include_router(collaborations_router)
router.include_router(commercial_growth_router)
router.include_router(brand_revenue_os_router)
router.include_router(shop_automation_router)
router.include_router(role_dashboard_router)
router.include_router(owner_operations_intelligence_router)
router.include_router(owner_focus_router)
router.include_router(chat9_product_router)
router.include_router(creator_plan_router)
router.include_router(live_show_planner_router)
router.include_router(niche_discovery_router)
router.include_router(creator_data_import_router)
router.include_router(progress_intelligence_router)
router.include_router(creator_reviews_router)
router.include_router(support_center_router)
router.include_router(support_sla_router)
router.include_router(broadcast_tech_router)
router.include_router(shared_sky_router)
router.include_router(shared_sky_transport_router)
router.include_router(shared_sky_internal_media_router)
router.include_router(creator_tech_vault_router)
router.include_router(service_registry_router)
router.include_router(social_media_library_router)
router.include_router(universal_creative_library_router)
router.include_router(aura_os_router)


def _route_mounted(path: str, method: str) -> bool:
    return any(
        getattr(route, "path", None) == path and method in (getattr(route, "methods", None) or set())
        for route in router.routes
    )


if not _route_mounted("/owner/shared-sky/api/scheduler/status", "GET"):
    router.add_api_route(
        "/owner/shared-sky/api/scheduler/status",
        scheduler_status,
        methods=["GET"],
        tags=["Shared Sky Streaming Studios Scheduler"],
    )
if not _route_mounted("/owner/shared-sky/api/scheduler/run-due", "POST"):
    router.add_api_route(
        "/owner/shared-sky/api/scheduler/run-due",
        scheduler_run_due,
        methods=["POST"],
        tags=["Shared Sky Streaming Studios Scheduler"],
    )


@router.get("/command-center/level-up", response_class=HTMLResponse, include_in_schema=False)
def level_up_with_creator_plan(request: Request):
    """Expose the integrated private ESP Creator/Shared Hub surfaces from Level Up."""
    response = base_level_up_portal(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    progress_link = "<a class='btn' href='/command-center/progress'>Progress</a>"
    links = [
        "<a class='btn primary' href='/shared-sky'>Shared Sky Streaming Studios</a>",
        "<a class='btn primary' href='/command-center/dashboard'>Creator / Agent Dashboard</a>",
        "<a class='btn primary' href='/command-center/member-hub'>ESP Member Hub</a>",
        "<a class='btn primary' href='/command-center/onboarding'>Creator Onboarding &amp; Public Profile</a>",
        "<a class='btn primary' href='/command-center/training'>Training Academy</a>",
        "<a class='btn' href='/command-center/library'>Academy &amp; Resources</a>",
        "<a class='btn' href='/command-center/incentives'>Incentives</a>",
        "<a class='btn' href='/command-center/collaborations'>Collaborations</a>",
        "<a class='btn' href='/command-center/commerce'>Commerce &amp; Shop</a>",
        "<a class='btn primary' href='/command-center/shop-automation'>Shop Automation</a>",
        "<a class='btn' href='/command-center/brands'>Brands</a>",
        "<a class='btn primary' href='/command-center/my-plan'>My Plan</a>",
        "<a class='btn primary' href='/command-center/niche-lab'>Niche Discovery Lab</a>",
        "<a class='btn primary' href='/command-center/show-planner'>LIVE Show Planner</a>",
        "<a class='btn primary' href='/command-center/progress/import'>Performance Data Import</a>",
        "<a class='btn primary' href='/command-center/progress/intelligence'>Progress Intelligence</a>",
        "<a class='btn' href='/command-center/my-plan/reviews'>30/60/90 Reviews</a>",
        "<a class='btn' href='/command-center/support'>Support &amp; Evidence</a>",
        "<a class='btn' href='/command-center/broadcast-tech'>Broadcast &amp; Tech Desk</a>",
        "<a class='btn' href='/command-center/social/media-library'>Social Media Library</a>",
    ]
    if progress_link in html:
        missing = [link for link in links if link.split("href='")[1].split("'")[0] not in html]
        if missing:
            html = html.replace(progress_link, "".join(missing) + progress_link, 1)

    replacements = (
        (
            "<div class='topline'><span>Creator OS</span><b class='planned'>Designed</b></div><h3>My Plan &amp; Activation Pathway</h3>",
            "<div class='topline'><span>Creator OS</span><b class='live'>Built</b></div><h3>My Plan &amp; Activation Pathway</h3>",
        ),
        (
            "<div class='topline'><span>Support &amp; Safety</span><b class='planned'>Designed</b></div><h3>Support, Safety, Violations &amp; Evidence</h3>",
            "<div class='topline'><span>Support &amp; Safety</span><b class='partial'>Partially Built</b></div><h3>Support, Safety, Violations &amp; Evidence</h3>",
        ),
        (
            "<div class='topline'><span>Creator Technology</span><b class='planned'>Designed</b></div><h3>ESP Pro Broadcast &amp; Tech Desk</h3>",
            "<div class='topline'><span>Creator Technology</span><b class='partial'>Partially Built</b></div><h3>ESP Pro Broadcast &amp; Tech Desk</h3>",
        ),
    )
    for source, target in replacements:
        if source in html:
            html = html.replace(source, target, 1)
    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
