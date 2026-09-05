from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio.creation_live import router as base_router
from aura_music_studio.creation_live_authority import router as authority_router
from aura_music_studio.creation_live_community import router as community_router
from aura_music_studio.route_integrity import deduplicate_http_routes, duplicate_http_signatures


def _chat7_routes(app: FastAPI):
    return [
        route
        for route in app.router.routes
        if str(getattr(route, "path", "")).startswith("/creation-live")
    ]


def _module_router_sizes() -> tuple[int, int, int]:
    return len(base_router.routes), len(authority_router.routes), len(community_router.routes)


def test_chat7_route_composition_is_isolated_between_fastapi_apps():
    """Reconciling one app must never consume or poison the module router templates."""
    before = _module_router_sizes()

    first = FastAPI()
    deduplicate_http_routes(first)
    deduplicate_http_routes(first)

    second = FastAPI()
    deduplicate_http_routes(second)
    deduplicate_http_routes(second)

    assert _module_router_sizes() == before
    for app in (first, second):
        routes = _chat7_routes(app)
        assert routes
        assert any(getattr(route, "path", None) == "/creation-live/capabilities" for route in routes)
        assert any(
            getattr(route, "path", None)
            == "/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach"
            and route.endpoint.__module__ == "aura_music_studio.creation_live_authority"
            for route in routes
        )
        assert any(
            getattr(route, "path", None) == "/creation-live/projects/{project_name}/community"
            and route.endpoint.__module__ == "aura_music_studio.creation_live_community"
            for route in routes
        )
        assert duplicate_http_signatures(app.router.routes) == {}


def test_stale_creation_live_state_marker_cannot_suppress_routes():
    app = FastAPI()
    app.state.creation_live_installed = True
    app.state.creation_live_authority_routes_installed = True
    app.state.creation_live_community_route_installed = True

    deduplicate_http_routes(app)

    paths = {getattr(route, "path", None) for route in _chat7_routes(app)}
    assert "/creation-live/capabilities" in paths
    assert "/creation-live/projects/{project_name}/community" in paths
    assert "/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach" in paths
