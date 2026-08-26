from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .esp_creator_plan import router as creator_plan_router
from .esp_creator_reviews import router as creator_reviews_router
from .esp_level_up import level_up_portal as base_level_up_portal
from .esp_support_center import router as support_center_router

router = APIRouter()
router.include_router(creator_plan_router)
router.include_router(creator_reviews_router)
router.include_router(support_center_router)


@router.get("/command-center/level-up", response_class=HTMLResponse, include_in_schema=False)
def level_up_with_creator_plan(request: Request):
    """Expose My Plan, milestone reviews and private support from the ESP Level Up Hub."""
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
    if progress_link in html and "/command-center/my-plan" not in html:
        html = html.replace(progress_link, my_plan_link + reviews_link + support_link + progress_link, 1)
    else:
        if progress_link in html and "/command-center/my-plan/reviews" not in html:
            html = html.replace(progress_link, reviews_link + progress_link, 1)
        if progress_link in html and "/command-center/support" not in html:
            html = html.replace(progress_link, support_link + progress_link, 1)

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

    return HTMLResponse(html, status_code=response.status_code, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
