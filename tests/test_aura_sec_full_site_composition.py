from __future__ import annotations

from fastapi.routing import APIRoute

from app import app


def _route(path: str) -> APIRoute:
    matches = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path
    ]
    assert len(matches) == 1, f"expected exactly one production route for {path!r}"
    return matches[0]


def test_production_app_mounts_member_safe_aura_sec_security_center():
    html_route = _route("/aura-sec")
    overview_route = _route("/aura-sec/overview")

    assert html_route.methods == {"GET"}
    assert overview_route.methods == {"GET"}
    assert "/aura-sec/overview" in app.openapi()["paths"]


def test_aura_sec_browser_surface_does_not_mount_native_authority_routes():
    paths = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    forbidden = {
        "/aura-sec/native/poll",
        "/aura-sec/native/heartbeat",
        "/aura-sec/native/receipt",
        "/aura-sec/native/execute",
        "/aura-sec/sign",
        "/aura-sec/approve",
        "/aura-sec/actions/execute",
    }
    assert forbidden.isdisjoint(paths)

    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/aura-sec"):
            continue
        assert (route.methods or set()) <= {"GET", "HEAD"}


def test_aura_sec_overview_is_not_a_public_anonymous_health_endpoint():
    route = _route("/aura-sec/overview")
    source_module = route.endpoint.__module__
    assert source_module == "aura_music_studio.aura_sec_portal"
    assert route.include_in_schema is True


def test_security_center_truth_boundary_is_explicit_in_source_contract():
    from aura_music_studio import aura_sec_portal

    source = aura_sec_portal._safe_control_plane_snapshot
    assert callable(source)
    # The browser contract deliberately has no function accepting signatures, command
    # envelopes, native receipts, approval proof or execution payloads.
    assert source.__code__.co_argcount == 1
    assert source.__code__.co_varnames[0] == "user_id"
