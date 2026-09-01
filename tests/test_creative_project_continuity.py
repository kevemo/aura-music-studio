from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import app as production_entrypoint
from aura_music_studio.creative_project_continuity import (
    CreativeProjectContinuityMiddleware,
    PROJECT_CONTINUITY_SCRIPT,
    router,
)


SURFACES = (
    "/creative-house",
    "/image-designer",
    "/video-studio",
    "/studio",
    "/game-creation",
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)

    for route_path in SURFACES:
        def page(path=route_path):
            return HTMLResponse(f"<html><body><main class='wrap'><a href='/studio'>Music</a>{path}</main></body></html>")

        app.add_api_route(route_path, page, methods=["GET"], response_class=HTMLResponse)

    app.add_middleware(CreativeProjectContinuityMiddleware)
    return TestClient(app)


def test_continuity_script_is_injected_into_all_five_creative_surfaces():
    client = _client()
    marker = "<script src='/creative/project-continuity-ui.js'></script>"

    for path in SURFACES:
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.text


def test_continuity_script_exposes_one_project_workspace_and_safe_query_handoff():
    client = _client()
    response = client.get("/creative/project-continuity-ui.js")

    assert response.status_code == 200
    script = response.text
    assert "One Project Workspace" in script
    assert "new URLSearchParams(location.search).get('project')" in script
    assert "history.replaceState" in script
    assert "encodeURIComponent(clean)" in script
    assert "preserveExistingLinks" in script
    assert "CreativeProjectContinuity" in script
    for path in SURFACES:
        assert repr(path) in script or f"'{path}'" in script


def test_media_studios_auto_load_exact_requested_project_without_creating_one():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "if(isMedia)" in script
    assert "input.value=requested" in script
    assert "await loadProject(true)" in script
    assert "initialize" not in script.split("async function bootRequestedProject", 1)[1].split("if(isHouse)", 1)[0]


def test_music_studio_selects_only_an_exact_existing_project():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "await refreshProjects()" in script
    assert "projects.some(item=>String(item?.name||'')===requested)" in script
    assert "if(exists)selectProject(requested)" in script


def test_game_forge_carries_context_without_guessing_from_display_titles():
    script = PROJECT_CONTINUITY_SCRIPT

    game_section = script.split("if(isGame){", 1)[1].split("}", 1)[0]
    assert "contextualProject=requested" in game_section
    assert "project_title" not in game_section
    assert "title" not in game_section.lower()


def test_production_entrypoint_mounts_continuity_router_and_middleware():
    paths = {getattr(route, "path", None) for route in production_entrypoint.app.router.routes}
    assert "/creative/project-continuity-ui.js" in paths

    middleware_classes = {entry.cls for entry in production_entrypoint.app.user_middleware}
    assert CreativeProjectContinuityMiddleware in middleware_classes
