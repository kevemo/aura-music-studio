from __future__ import annotations

import subprocess
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

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
    media_boot = script.split("async function bootRequestedProject", 1)[1].split("if(isHouse)", 1)[0]
    assert "initializeProject" not in media_boot
    assert "initProject" not in media_boot


def test_music_studio_selects_only_an_exact_existing_project():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "await refreshProjects()" in script
    assert "projects.some(item=>String(item?.name||'')===requested)" in script
    assert "if(exists)selectProject(requested)" in script


def test_game_forge_carries_context_without_guessing_project_identity():
    script = PROJECT_CONTINUITY_SCRIPT

    game_section = script.split("if(isGame){", 1)[1].split("}\n", 1)[0]
    assert "contextualProject=requested" in game_section
    assert "project_title" not in game_section
    assert "project.name" not in game_section


def test_middleware_does_not_inject_script_into_non_html_or_non_target_routes():
    client = _client()

    response = client.get("/creative/project-continuity-ui.js")
    assert "<script src='/creative/project-continuity-ui.js'></script>" not in response.text


def test_production_entrypoint_mounts_continuity_router_and_middleware():
    # Validate the repository-root production entrypoint in a fresh interpreter. FastAPI 0.141
    # preserves include_router() composition lazily, so hidden routes must not be asserted by
    # expecting every APIRoute to appear flattened in app.router.routes. Instead prove all three
    # composition facts directly: the imported router owns the hidden endpoint, root app.py calls
    # include_router() for that exact alias, and the runtime middleware stack contains the class.
    probe = """
import importlib.util
from pathlib import Path

root_app = Path.cwd() / 'app.py'
source = root_app.read_text(encoding='utf-8')
spec = importlib.util.spec_from_file_location('command_center_production_entrypoint', root_app)
assert spec is not None and spec.loader is not None, root_app
production_entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(production_entrypoint)

from aura_music_studio.creative_project_continuity import (
    CreativeProjectContinuityMiddleware,
    router as continuity_router,
)

assert production_entrypoint.creative_project_continuity_router is continuity_router
assert any(
    getattr(route, 'path', None) == '/creative/project-continuity-ui.js'
    for route in continuity_router.routes
), 'continuity router does not own the hidden JS endpoint'
assert 'app.include_router(creative_project_continuity_router)' in source, (
    'canonical root app.py does not include the continuity router alias'
)
middleware_classes = {entry.cls for entry in production_entrypoint.app.user_middleware}
assert CreativeProjectContinuityMiddleware in middleware_classes, middleware_classes
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
