from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .esp_broadcast_tech import router as broadcast_tech_router
from .esp_creator_plan import router as creator_plan_router
from .esp_creator_reviews import router as creator_reviews_router
from .esp_level_up import level_up_portal as base_level_up_portal
from .esp_social_media_library import router as social_media_library_router
from .esp_support_center import router as support_center_router

router = APIRouter()
router.include_router(creator_plan_router)
router.include_router(creator_reviews_router)
router.include_router(support_center_router)
router.include_router(broadcast_tech_router)
router.include_router(social_media_library_router)


@router.get("/command-center/level-up", response_class=HTMLResponse, include_in_schema=False)
def level_up_with_creator_plan(request: Request):
    """Expose Creator OS, private support, broadcast tech and Social Media Library from the ESP Level Up Hub."""
    response = base_level_up_portal(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    progress_link = "<a class='btn' href='/command-center/progress'>Progress</a>"
    my_plan_link = "<a class='btn primary' href='/command-center/my-plan'>My Plan</a>"
    reviews_link = "<a class='btn' href='/command-center/my-plan/reviews'>30/60/90 Reviews</a>"
    support_link = "<a class='btn' href='/command-center/support'>Support &amp; Evidence</a>"
    broadcast_link = "<a class='btn' href='/command-center/broadcast-tech'>Broadcast &amp; Tech Desk</a>"
    media_link = "<a class='btn' href='/command-center/social/media-library'>Social Media Library</a>"
    if progress_link in html and "/command-center/my-plan" not in html:
        html = html.replace(progress_link, my_plan_link + reviews_link + support_link + broadcast_link + media_link + progress_link, 1)
    else:
        if progress_link in html and "/command-center/my-plan/reviews" not in html:
            html = html.replace(progress_link, reviews_link + progress_link, 1)
        if progress_link in html and "/command-center/support" not in html:
            html = html.replace(progress_link, support_link + progress_link, 1)
        if progress_link in html and "/command-center/broadcast-tech" not in html:
            html = html.replace(progress_link, broadcast_link + progress_link, 1)
        if progress_link in html and "/command-center/social/media-library" not in html:
            html = html.replace(progress_link, media_link + progress_link, 1)

    designed = (
        "<div class='topline'><span>Creator OS</span><b class='planned'>Designed</b></div>"
        "<h3>My Plan &amp; Activation Pathway</h3>"
    )
    built = (
        "<div class='topline'><span>Creator OS</span><b class='live'>Built</b></div>"
        "<h3>My Plan &amp; Activation Pathway</h3>"
    )
    if designed in html:
        html = html.replace(designed, built, 1)

    support_designed = (
        "<div class='topline'><span>Support &amp; Safety</span><b class='planned'>Designed</b></div>"
        "<h3>Support, Safety, Violations &amp; Evidence</h3>"
    )
    support_partial = (
        "<div class='topline'><span>Support &amp; Safety</span><b class='partial'>Partially Built</b></div>"
        "<h3>Support, Safety, Violations &amp; Evidence</h3>"
    )
    if support_designed in html:
        html = html.replace(support_designed, support_partial, 1)

    broadcast_designed = (
        "<div class='topline'><span>Creator Technology</span><b class='planned'>Designed</b></div>"
        "<h3>ESP Pro Broadcast &amp; Tech Desk</h3>"
    )
    broadcast_partial = (
        "<div class='topline'><span>Creator Technology</span><b class='partial'>Partially Built</b></div>"
        "<h3>ESP Pro Broadcast &amp; Tech Desk</h3>"
    )
    if broadcast_designed in html:
        html = html.replace(broadcast_designed, broadcast_partial, 1)

    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
