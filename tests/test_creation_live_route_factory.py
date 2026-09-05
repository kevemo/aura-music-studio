from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio import creation_live as cl
from aura_music_studio import creation_live_authority as authority
from aura_music_studio import creation_live_community as community
from aura_music_studio.route_integrity import deduplicate_http_routes, duplicate_http_signatures


EXPECTED = {
    ("/creation-live/capabilities", ("GET",)),
    ("/creation-live/projects/{project_name}/sources", ("GET",)),
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}", ("GET",)),
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}/media", ("GET",)),
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach", ("POST",)),
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}/transition", ("POST",)),
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}/emergency-hide", ("POST",)),
    ("/creation-live/projects/{project_name}/sources/{source_adapter_id}/detach", ("POST",)),
    ("/creation-live/shared-sky/broadcasts", ("GET",)),
    ("/creation-live/projects/{project_name}/markers", ("POST",)),
    ("/creation-live/projects/{project_name}/returns", ("POST",)),
    ("/creation-live/projects/{project_name}/community", ("GET",)),
    ("/creation-live/projects/{project_name}/aura-assistance", ("GET",)),
    ("/creation-live/ui.js", ("GET",)),
}


def _signatures(app: FastAPI) -> set[tuple[str, tuple[str, ...]]]:
    rows: set[tuple[str, tuple[str, ...]]] = set()
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if isinstance(path, str) and path.startswith("/creation-live") and methods:
            rows.add((path, tuple(sorted(str(method).upper() for method in methods))))
    return rows


def _assert_authority(app: FastAPI) -> None:
    assert _signatures(app) == EXPECTED
    assert duplicate_http_signatures(app.router.routes) == {}
    by_path = {getattr(route, "path", None): route for route in app.router.routes}
    assert by_path["/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach"].endpoint is authority.authoritative_attach
    assert by_path["/creation-live/projects/{project_name}/markers"].endpoint is authority.authoritative_marker
    assert by_path["/creation-live/projects/{project_name}/returns"].endpoint is authority.authoritative_return
    assert by_path["/creation-live/projects/{project_name}/community"].endpoint is community.authoritative_community_panel


def test_route_composition_does_not_depend_on_mutable_module_router_lists(monkeypatch):
    monkeypatch.setattr(cl.router, "routes", [])
    monkeypatch.setattr(authority.router, "routes", [])
    monkeypatch.setattr(community.router, "routes", [])

    app = FastAPI()
    deduplicate_http_routes(app)
    _assert_authority(app)


def test_canonical_composition_does_not_depend_on_include_router(monkeypatch):
    app = FastAPI()
    calls: list[object] = []

    def ignored_include_router(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    # The production overlay can snapshot late routers. Chat 7 therefore binds its canonical
    # endpoint functions with app.add_api_route and must remain complete even if include_router is
    # ineffective at this stage of composition.
    monkeypatch.setattr(app, "include_router", ignored_include_router)
    deduplicate_http_routes(app)

    assert calls == []
    _assert_authority(app)


def test_direct_route_composition_is_idempotent_across_multiple_apps():
    first = FastAPI()
    second = FastAPI()

    deduplicate_http_routes(first)
    deduplicate_http_routes(first)
    deduplicate_http_routes(second)

    _assert_authority(first)
    _assert_authority(second)
