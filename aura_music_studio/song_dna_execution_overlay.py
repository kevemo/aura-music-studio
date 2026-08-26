from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .daw_fx_lab import router as daw_fx_lab_router
from .performance_generation_portal import router as performance_generation_router
from .song_dna_focus_locks import router as focus_locks_router
from .song_dna_portal import song_editor_project as base_song_editor_project
from .voice_house_api import router as voice_house_api_router
from .voice_house_assets_api import router as voice_house_assets_router
from .voice_house_portal import router as voice_house_portal_router

router = APIRouter()
router.include_router(daw_fx_lab_router)
router.include_router(performance_generation_router)
router.include_router(focus_locks_router)
router.include_router(voice_house_api_router)
router.include_router(voice_house_assets_router)
router.include_router(voice_house_portal_router)


@router.get("/song-editor/{project_name}", response_class=HTMLResponse, include_in_schema=False)
def song_editor_with_execution(project_name: str, request: Request):
    """Add execution, alignment, performance generation, FX Lab, focus locks and Voice House to Song DNA."""
    response = base_song_editor_project(project_name, request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    encoded = quote(project_name, safe="")
    buttons = (
        f"<a class='btn good' href='/song-editor/{encoded}/audition'>Generate · Audition · Commit</a> "
        f"<a class='btn' href='/song-editor/{encoded}/performance'>Performance → Song</a> "
        f"<a class='btn' href='/fx-lab?project={encoded}'>Instrument &amp; FX Lab</a> "
        f"<a class='btn' href='/voice-house/{encoded}'>Voice House</a> "
        f"<a class='btn' href='/song-editor/{encoded}/alignment'>Lyric Alignment</a> "
        f"<a class='btn' href='/song-editor/{encoded}/focus-locks'>Protect Everything Except…</a> "
    )
    marker = "<a class='btn' href='/daw'>Open DAW</a>"
    if marker in html:
        html = html.replace(marker, buttons + marker, 1)
    else:
        html = html.replace(
            "<main class='wrap'>",
            f"<main class='wrap'><div style='padding-top:12px'>{buttons}</div>",
            1,
        )
    return HTMLResponse(html, status_code=response.status_code)
