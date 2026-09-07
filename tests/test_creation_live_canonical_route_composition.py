from __future__ import annotations

from fastapi.routing import APIRoute

from app import app


def _matching(path: str, method: str) -> list[APIRoute]:
    normalized = method.upper()
    return [
        route
        for route in app.router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and normalized in (route.methods or set())
    ]


def test_creation_live_routes_have_singular_canonical_dispatch_authority():
    expected = [
        ("GET", "/creation-live/capabilities"),
        ("GET", "/creation-live/projects/{project_name}/sources"),
        ("GET", "/creation-live/projects/{project_name}/sources/{source_adapter_id}"),
        ("GET", "/creation-live/projects/{project_name}/sources/{source_adapter_id}/media"),
        ("POST", "/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach"),
        ("POST", "/creation-live/projects/{project_name}/sources/{source_adapter_id}/transition"),
        ("POST", "/creation-live/projects/{project_name}/sources/{source_adapter_id}/emergency-hide"),
        ("POST", "/creation-live/projects/{project_name}/sources/{source_adapter_id}/detach"),
        ("GET", "/creation-live/shared-sky/broadcasts"),
        ("POST", "/creation-live/projects/{project_name}/markers"),
        ("POST", "/creation-live/projects/{project_name}/returns"),
        ("GET", "/creation-live/projects/{project_name}/community"),
        ("GET", "/creation-live/projects/{project_name}/aura-assistance"),
        ("GET", "/creation-live/ui.js"),
    ]
    for method, path in expected:
        matches = _matching(path, method)
        assert len(matches) == 1, f"expected one canonical {method} {path}, found {len(matches)}"


def test_creation_live_final_composition_preserves_newer_domain_hooks():
    assert app.state.creation_live_installed is True
    assert app.state.creation_live_authority_routes_installed is True
    assert app.state.creation_live_community_route_installed is True
    diagnostics = app.state.route_integrity
    assert "game_forge_project_routes" in diagnostics["composition_hooks_run"]
