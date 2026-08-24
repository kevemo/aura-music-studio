from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .social_management_portal import social_house as base_social_house

router = APIRouter()


@router.get("/command-center/social", response_class=HTMLResponse, include_in_schema=False)
def social_house_with_intelligence(request: Request):
    """Preserve the mature Social Management UI and add the new intelligence workspace."""
    response = base_social_house(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    marker = "<a class='btn optional' href='/command-center/niche'>Change Niche</a>"
    extra = "<a class='btn primary' href='/command-center/social-insights'>Analytics & Aura Insights</a>"
    if marker in html and extra not in html:
        html = html.replace(marker, marker + extra, 1)
    return HTMLResponse(html, status_code=response.status_code)
