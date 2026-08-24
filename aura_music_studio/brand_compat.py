from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["ESP Brand"])


@router.get("/brand/logo.webp", include_in_schema=False)
def logo_alias():
    """Stable shorthand used by newer Studio workspaces."""
    return RedirectResponse("/brand/esp-logo.webp", status_code=307)
