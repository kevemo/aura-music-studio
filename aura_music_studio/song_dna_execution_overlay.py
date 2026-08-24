from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .song_dna_portal import song_editor_project as base_song_editor_project

router = APIRouter()


@router.get("/song-editor/{project_name}", response_class=HTMLResponse, include_in_schema=False)
def song_editor_with_execution(project_name: str, request: Request):
    """Add the non-destructive audition/commit console to the existing Song DNA page.

    The mature editor remains the source view. This wrapper only injects the new execution
    link so we do not duplicate or fork its lyric/section/instrument editing UI.
    """
    response = base_song_editor_project(project_name, request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response
    encoded = quote(project_name, safe="")
    button = (
        f"<a class='btn good' href='/song-editor/{encoded}/audition'>"
        "Generate · Audition · Commit</a> "
    )
    marker = "<a class='btn' href='/daw'>Open DAW</a>"
    if marker in html:
        html = html.replace(marker, button + marker, 1)
    else:
        html = html.replace(
            "<main class='wrap'>",
            f"<main class='wrap'><div style='padding-top:12px'>{button}</div>",
            1,
        )
    return HTMLResponse(html, status_code=response.status_code)
